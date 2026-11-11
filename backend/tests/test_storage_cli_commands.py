"""Storage transfer commands keep every URI and path one literal argument.

The copy/sync/auth methods return shell text that init containers, sidecars, EXIT traps and build
templates run. Their operands used to be interpolated bare, so an uploaded file named
``a$(id).fq`` ran ``id``. These tests run each generated command under ``/bin/sh`` with a recording
``gcloud``/``gsutil``/``aws`` on PATH and compare the argv the CLI received; NFS commands run the
real ``cp`` against a temporary mount root and compare bytes.

``gcloud storage`` also reads ``*``, ``?`` and ``[`` in a source as a pattern (checked with
``google/cloud-sdk:slim`` 593.0.0: a literal ``a[1].txt`` selected ``a1.txt``); a character class
(``[[]``, ``[*]``, ``[?]``) is its only escape, so the GCS adapter writes literal sources that way.
"""

from __future__ import annotations

import os
import shlex
import subprocess

import pytest

from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.exceptions import ValidationError
from app.platform.shell_args import ShellExpr

HOSTILE_NAMES = [
    "a$(touch PWNED).fq",
    "a`touch PWNED`.fq",
    "a;touch PWNED;.fq",
    "a b.fq",
    "it's.fq",
    'a"b.fq',
    "a\nb.fq",
    "-rf.fq",
    "a|b&c>d.fq",
]

RECORDER = """#!/bin/sh
for a in "$@"; do printf '%s\\0' "$a"; done >> "$RECORD"
printf '\\n' >> "$RECORD"
"""

# gcloud as the verified-fetch helper uses it: a token, no endpoint override, and `storage hash`
# answered with a stand-in digest (POSIX cksum) that the fake JSON API also reports.
GCLOUD = (
    RECORDER
    + r"""case "$1 $2" in
  "auth print-access-token") echo fake-token ;;
  "storage hash")
    # Like the real CLI: `[*?` in a local path is a pattern, so the path arrives escaped.
    case $3 in *'[[]'*|*'[*]'*|*'[?]'*) ;; *[][*?]*) echo "No URLS matched" >&2; exit 1 ;; esac
    cksum < "$(printf '%s' "$3" | sed 's/[[]\([][*?]\)[]]/\1/g')" | awk '{ print $1 }' ;;
esac
"""
)

# The JSON API as `curl` reaches it: objects live under $FAKE_GCS/<bucket>/<key>, the path
# segments are percent-encoded, the token must arrive on stdin (`-K -`), never in argv.
FAKE_CURL = """#!/usr/bin/env python3
import os, subprocess, sys, urllib.parse
args = sys.argv[1:]
with open(os.environ["CURL_RECORD"], "a") as rec:
    rec.write(repr(args) + "\\n")
config = sys.stdin.read() if "-K" in args else ""
if "fake-token" not in config or any("fake-token" in a for a in args):
    sys.exit("token must come from stdin config only")
url = args[-1]
out = args[args.index("-o") + 1] if "-o" in args else None
path, _, query = url.partition("?")
bucket, key = path.split("/storage/v1/b/", 1)[1].split("/o/", 1)
obj = os.path.join(os.environ["FAKE_GCS"], urllib.parse.unquote(bucket), urllib.parse.unquote(key))
if not os.path.isfile(obj):
    sys.stderr.write("curl: (22) The requested URL returned error: 404\\n")
    sys.exit(22)
if query == "fields=crc32c":
    digest = subprocess.run(["cksum"], stdin=open(obj, "rb"), capture_output=True, text=True).stdout.split()[0]
    print('{\\n  "crc32c": "%s"\\n}' % digest)
elif query == "alt=media":
    data = open(obj, "rb").read()
    if os.environ.get("FAKE_CORRUPT"):
        data += b"!"
    open(out, "wb").write(data)
else:
    sys.exit("unexpected request " + url)
"""


class Shell:
    """Runs generated shell text with recording CLIs and no other side effects allowed."""

    def __init__(self, tmp_path):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.record = tmp_path / "argv"
        self.curl_record = tmp_path / "curl-argv"
        self.store = tmp_path / "fake-gcs"
        self.store.mkdir()
        self.env: dict[str, str] = {}
        for tool, text in (("gcloud", GCLOUD), ("gsutil", RECORDER), ("aws", RECORDER), ("curl", FAKE_CURL)):
            (self.bin / tool).write_text(text)
            (self.bin / tool).chmod(0o755)
        self.cwd = tmp_path / "cwd"
        self.cwd.mkdir()

    def put(self, uri: str, data: bytes) -> None:
        """An object the fake JSON API serves."""
        bucket, key = uri.removeprefix("gs://").split("/", 1)
        path = self.store / bucket / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def run(self, script: str, *, check: bool = True) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "RECORD": str(self.record),
            "CURL_RECORD": str(self.curl_record),
            "FAKE_GCS": str(self.store),
            "HOME": str(self.root),
            **self.env,
        }
        result = subprocess.run(["/bin/sh", "-c", script], cwd=self.cwd, env=env, capture_output=True, timeout=20)
        assert os.listdir(self.cwd) == [], f"side effects: {os.listdir(self.cwd)}"
        if check:
            assert result.returncode == 0, result.stderr
        return result

    def calls(self) -> list[list[str]]:
        if not self.record.exists():
            return []
        lines = self.record.read_bytes().split(b"\0\n")
        return [line.decode().split("\0") for line in lines if line]

    def copies(self) -> list[list[str]]:
        """The CLI copy/fetch calls, without the helper's token, endpoint and hash calls."""
        return [c for c in self.calls() if c[:2] not in (["auth", "print-access-token"], ["storage", "hash"])]


GCS_LINE_BREAK_EXCLUDE = "--exclude=(?s).*[\\r\\n].*"


def gcs_escape(name: str) -> str:
    return name.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


def needs_api_fetch(uri: str, dest: str) -> bool:
    """A name `gcloud`/`gsutil` cannot represent: a source key with `[*?#` (`#123` is read as a
    generation) or a destination with `[*?` (refused as a wildcard)."""
    key = uri.removeprefix("gs://").split("/", 1)[1]
    return any(c in key for c in "[*?#") or any(c in dest for c in "[*?")


def assert_gcs_fetched(sh, pairs, cp=("storage", "cp")) -> None:
    """Plain names were copied by the CLI (source escaped); the rest were fetched through the
    JSON API and hold the object's exact bytes."""
    cp = list(cp)
    assert [c for c in sh.copies() if c[: len(cp)] == cp] == [
        cp + [gcs_escape(uri), dest] for uri, dest in pairs if not needs_api_fetch(uri, dest)
    ]
    for uri, dest in pairs:
        if needs_api_fetch(uri, dest):
            bucket, key = uri.removeprefix("gs://").split("/", 1)
            with open(dest, "rb") as f:
                assert f.read() == (sh.store / bucket / key).read_bytes(), dest


@pytest.fixture
def sh(tmp_path):
    return Shell(tmp_path)


class TestCloudCopyCommands:
    @pytest.mark.parametrize("name", HOSTILE_NAMES)
    def test_gcs_copy_in_passes_both_operands_literally(self, sh, name):
        uri, dest = f"gs://b/uploads/{name}", f"/data/x/{name}"
        sh.run(GcsStorageProvider().cli_copy_in(uri, dest))
        assert sh.calls() == [["storage", "cp", gcs_escape(uri), dest]]


class TestGcsVerifiedFetch:
    """Downloads `gcloud` cannot name go through the JSON API, verified, then published.

    On blackbird `gcloud storage cp` refused a local destination `.../in [1] ... .txt` ("contains an
    unexpected wildcard", L1), the owner reproduced `artifact#123` read as generation 123, and the
    first fix (`gcloud storage cat > dest`) had no checksum and truncated an existing destination
    when the object was missing. The helper reads the object's crc32c, downloads into a temporary
    file beside the destination, compares `gcloud storage hash`, and only then moves it into place.
    """

    @pytest.mark.parametrize(
        "key,dest_name",
        [
            ("u/artifact#123", "artifact#123"),
            ("u/in [1].txt", "in [1].txt"),
            ("u/s*.txt", "s*.txt"),
            ("u/q?.txt", "q?.txt"),
            ("u/plain.txt", "in [x].txt"),
            ("u/a b/#1?[x] it's $(touch PWNED).txt", "x.txt"),
        ],
    )
    def test_names_the_cli_cannot_represent_arrive_exactly(self, sh, tmp_path, key, dest_name):
        uri = f"gs://b/{key}"
        data = b"\x00binary\xff" + key.encode()
        sh.put(uri, data)
        dest = tmp_path / "sub [x]" / dest_name
        dest.parent.mkdir()
        sh.run(GcsStorageProvider().cli_copy_in(uri, str(dest)))
        assert dest.read_bytes() == data
        assert [c for c in sh.copies() if c[:2] == ["storage", "cp"]] == []
        assert os.listdir(dest.parent) == [dest_name]  # no temporary file left

    def test_plain_names_keep_the_cli_copy(self, sh, tmp_path):
        sh.run(GcsStorageProvider().cli_copy_in("gs://b/u/plain.txt", f"{tmp_path}/plain.txt"))
        assert sh.copies() == [["storage", "cp", "gs://b/u/plain.txt", f"{tmp_path}/plain.txt"]]
        assert not sh.curl_record.exists()

    def test_a_missing_object_fails_and_leaves_the_destination_alone(self, sh, tmp_path):
        dest = tmp_path / "in [1].txt"
        dest.write_bytes(b"earlier contents")
        result = sh.run(GcsStorageProvider().cli_copy_in("gs://b/u/gone#1", str(dest)), check=False)
        assert result.returncode != 0 and b"could not read gs://b/u/gone#1" in result.stderr
        assert dest.read_bytes() == b"earlier contents"
        assert not [n for n in os.listdir(tmp_path) if n.startswith(".bioaf-partial")]

    def test_a_checksum_mismatch_fails_and_publishes_nothing(self, sh, tmp_path):
        sh.put("gs://b/u/in [1].txt", b"payload")
        sh.env["FAKE_CORRUPT"] = "1"
        dest = tmp_path / "in [1].txt"
        dest.write_bytes(b"earlier contents")
        result = sh.run(GcsStorageProvider().cli_copy_in("gs://b/u/in [1].txt", str(dest)), check=False)
        assert result.returncode != 0 and b"checksum mismatch" in result.stderr
        assert dest.read_bytes() == b"earlier contents"
        assert not [n for n in os.listdir(tmp_path) if n.startswith(".bioaf-partial")]

    def test_the_token_never_reaches_a_command_line(self, sh, tmp_path):
        sh.put("gs://b/u/k#1", b"x")
        sh.run(GcsStorageProvider().cli_copy_in("gs://b/u/k#1", f"{tmp_path}/k"))
        assert "fake-token" not in sh.curl_record.read_text()
        assert "fake-token" not in sh.record.read_text(errors="replace")

    def test_a_runtime_destination_is_decided_by_the_shell(self, sh, tmp_path):
        sh.put("gs://b/u/k", b"payload")
        cmd = GcsStorageProvider().cli_copy_in("gs://b/u/k", ShellExpr('"$d"'))
        for dest in (f"{tmp_path}/a[1].txt", f"{tmp_path}/plain.txt"):
            sh.run(f"d={shlex.quote(dest)}; {cmd}")
        assert open(f"{tmp_path}/a[1].txt", "rb").read() == b"payload"
        assert [c for c in sh.copies() if c[:2] == ["storage", "cp"]] == [
            ["storage", "cp", "gs://b/u/k", f"{tmp_path}/plain.txt"]
        ]


class TestCloudCopyCommandsContinued:
    @pytest.mark.parametrize("name", ["a[1].fq", "st*r.fq", "q?.fq", "]x[.fq"])
    def test_gcs_sources_are_escaped_against_pattern_matching(self, sh, name):
        sh.run(GcsStorageProvider().cli_copy_out_file(f"/outputs/{name}", "gs://b/out/"))
        assert sh.calls() == [["storage", "cp", gcs_escape(f"/outputs/{name}"), "gs://b/out/"]]

    @pytest.mark.parametrize("name", HOSTILE_NAMES)
    def test_s3_copy_in_passes_both_operands_literally(self, sh, name):
        uri, dest = f"s3://b/uploads/{name}", f"/data/x/{name}"
        sh.run(S3StorageProvider().cli_copy_in(uri, dest))
        assert sh.calls() == [["s3", "cp", uri, dest]]

    def test_recursive_and_single_file_copy_out(self, sh):
        gcs, s3 = GcsStorageProvider(), S3StorageProvider()
        sh.run(gcs.cli_copy_out("/home/x y", "gs://b/p q/"))
        sh.run(s3.cli_copy_out("/home/x y", "s3://b/p q/"))
        sh.run(s3.cli_copy_out_file("/home/it's", "s3://b/p/"))
        assert sh.calls() == [
            ["storage", "cp", "-r", "/home/x y", "gs://b/p q/"],
            ["s3", "cp", "--recursive", "/home/x y", "s3://b/p q/"],
            ["s3", "cp", "/home/it's", "s3://b/p/"],
        ]

    @pytest.mark.parametrize(
        "provider,call",
        [
            (GcsStorageProvider, lambda p: p.cli_copy_in("s3://b/k", "/data/k")),
            (GcsStorageProvider, lambda p: p.cli_copy_in("gs://b/k", "data/k")),
            (GcsStorageProvider, lambda p: p.cli_copy_in("gs://b/k", "-data")),
            (S3StorageProvider, lambda p: p.cli_copy_out("/x", "gs://b/k")),
            (S3StorageProvider, lambda p: p.cli_copy_in("s3://b/k", "")),
            (GcsStorageProvider, lambda p: p.cli_copy_in("gs://b/k\0", "/x")),
        ],
    )
    def test_operands_that_are_not_this_backends_uri_or_an_absolute_path_are_refused(self, provider, call):
        with pytest.raises(ValidationError):
            call(provider())

    def test_a_runtime_value_is_the_callers_expression(self, sh):
        cmd = GcsStorageProvider().cli_copy_out_file(ShellExpr('"$f"'), "gs://b/scripts/")
        sh.run(f"f='/home/a b$(touch PWNED).py'; {cmd}")
        assert sh.calls() == [["storage", "cp", "/home/a b$(touch PWNED).py", "gs://b/scripts/"]]

    def test_contents_copy_names_the_directory_never_its_entries(self, sh, tmp_path):
        """No glob: the CLI walks the directory itself, so an entry's name is never re-read as a
        pattern (`gcloud storage rsync`, cloud-sdk 593.0.0 against a fake GCS server, copied
        `a[1].txt`, `s*.txt`, `q?.txt`, dotfiles and `-dash.txt` each as itself)."""
        outputs = tmp_path / "out [dir] $(touch PWNED)"
        outputs.mkdir()
        for n in ["plain.txt", "a[1].txt", "a1.txt", "s*.txt", ".hidden", "-rf"]:
            (outputs / n).write_text(n)
        sh.run(GcsStorageProvider().cli_copy_out_contents(str(outputs), "gs://b/run/"))
        sh.run(S3StorageProvider().cli_copy_out_contents(str(outputs), "s3://b/run/"))
        assert sh.calls() == [
            ["storage", "rsync", "--recursive", GCS_LINE_BREAK_EXCLUDE, str(outputs), "gs://b/run/"],
            ["s3", "cp", "--recursive", str(outputs), "s3://b/run/"],
        ]

    @pytest.mark.parametrize(
        "bad,shown",
        [
            (["with\nnewline.txt"], ["with\\nnewline.txt"]),
            (["with\rcarriage.txt"], ["with\\rcarriage.txt"]),
            (["crlf\r\n.txt"], ["crlf\\r\\n.txt"]),
            (["d\nx/f.txt", "c\ry/g.txt", "back\\slash\r.txt"], ["d\\nx/f.txt", "c\\ry/g.txt", "back\\\\slash\\r.txt"]),
        ],
        ids=["LF", "CR", "CRLF", "directories-and-backslash"],
    )
    def test_gcs_contents_copy_names_what_cloud_storage_cannot_hold_and_fails(self, sh, tmp_path, bad, shown):
        """Cloud Storage refuses CR and LF in an object name (blackbird: `HTTPError 400`; L3), and
        `gcloud storage rsync` crashes on either locally. rsync skips such paths and copies the
        rest; the copy then fails listing each one with CR and LF written as `\\r`/`\\n` (and a
        backslash doubled), so the list is unambiguous."""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        for rel in ["ok.txt", *bad]:
            (outputs / rel).parent.mkdir(parents=True, exist_ok=True)
            (outputs / rel).write_text("x")
        result = sh.run(GcsStorageProvider().cli_copy_out_contents(str(outputs), "gs://b/run"), check=False)
        assert result.returncode != 0
        assert sh.calls() == [["storage", "rsync", "--recursive", GCS_LINE_BREAK_EXCLUDE, str(outputs), "gs://b/run/"]]
        lines = result.stderr.decode().split("\n")
        assert (
            lines[0]
            == "bioAF: names with line breaks or carriage returns cannot be stored in Google Cloud Storage; not copied:"
        )
        assert sorted(lines[1:-1]) == sorted(f"  {outputs}/{s}" for s in shown)

    def test_the_rsync_exclusion_matches_exactly_cr_and_lf_paths(self):
        import re

        pattern = re.compile(GCS_LINE_BREAK_EXCLUDE.removeprefix("--exclude="))
        for path in ("a\nb", "a\rb", "a\r\nb", "d\nx/f", "d\rx/f"):
            assert pattern.fullmatch(path), repr(path)
        for path in ("ok.txt", "sub [x]/plain.txt", "a\\nb", "tab\tname"):
            assert not pattern.fullmatch(path), repr(path)

    def test_gcs_contents_copy_without_such_names_succeeds_quietly(self, sh, tmp_path):
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        (outputs / "ok.txt").write_text("x")
        result = sh.run(GcsStorageProvider().cli_copy_out_contents(str(outputs), "gs://b/run/"))
        assert result.stderr == b""

    def test_sync_commands_are_argv_with_tolerant_stage_in(self, sh):
        for provider in (GcsStorageProvider(), S3StorageProvider()):
            for argv in (
                provider.sync_in_command(provider.build_uri("w", "home/1 2/"), "/home/jo vyan"),
                provider.sync_out_command("/home/jo vyan", provider.build_uri("w", "home/1 2/")),
            ):
                assert argv[:2] == ["/bin/sh", "-c"]
                sh.run(argv[2])
        assert sh.calls() == [
            ["-m", "rsync", "-r", "gs://w/home/1 2/", "/home/jo vyan"],
            ["-m", "rsync", "-r", "/home/jo vyan", "gs://w/home/1 2/"],
            ["s3", "sync", "s3://w/home/1 2/", "/home/jo vyan"],
            ["s3", "sync", "/home/jo vyan", "s3://w/home/1 2/"],
        ]

    def test_auth_key_path_is_one_argument(self, sh):
        sh.run(GcsStorageProvider().cli_auth_command("/secrets/my key.json"))
        assert sh.calls() == [["auth", "activate-service-account", "--key-file=/secrets/my key.json", "--quiet"]]


class TestNfsCommands:
    """NFS commands are executed for real: the mapped paths must move the bytes."""

    @pytest.fixture
    def nfs(self, tmp_path):
        root = tmp_path / "mount root"
        root.mkdir()
        return NfsStorageProvider(root=str(root))

    @pytest.mark.parametrize("name", ["a b$(touch PWNED).fq", "it's", "-rf", "x\ny"])
    def test_copy_in_and_out_move_bytes_through_the_mapped_path(self, sh, nfs, tmp_path, name):
        uri = f"file://working/uploads/{name}"
        src = tmp_path / "local"
        src.mkdir()
        (src / "f").write_bytes(b"\x00\x01payload")
        sh.run(nfs.cli_copy_out_file(str(src / "f"), uri))
        sh.run(nfs.cli_copy_in(uri, str(tmp_path / "back")))
        assert (tmp_path / "back").read_bytes() == b"\x00\x01payload"
        assert sh.calls() == []  # no cloud CLI was involved

    def test_sync_round_trip_moves_bytes_both_ways(self, sh, nfs, tmp_path):
        home = tmp_path / "home dir"
        (home / "nb").mkdir(parents=True)
        (home / "nb" / "a b.ipynb").write_bytes(b"{}")
        (home / ".hidden").write_bytes(b"h")
        out = nfs.sync_out_command(str(home), "file://working/notebooks/7/")
        sh.run(out[2])
        restored = tmp_path / "restored"
        restored.mkdir()
        sh.run(nfs.sync_in_command("file://working/notebooks/7/", str(restored))[2])
        assert (restored / "nb" / "a b.ipynb").read_bytes() == b"{}"
        assert (restored / ".hidden").read_bytes() == b"h"

    def test_sync_in_tolerates_a_missing_prefix(self, sh, nfs, tmp_path):
        dest = tmp_path / "empty"
        dest.mkdir()
        sh.run(nfs.sync_in_command("file://working/notebooks/never/", str(dest))[2])
        assert os.listdir(dest) == []

    def test_an_unmounted_root_is_a_failure_not_a_tolerated_empty_prefix(self, sh, tmp_path):
        nfs = NfsStorageProvider(root=str(tmp_path / "not mounted"))
        dest = tmp_path / "d"
        dest.mkdir()
        for argv in (
            nfs.sync_in_command("file://working/notebooks/7/", str(dest)),
            nfs.sync_out_command(str(dest), "file://working/notebooks/7/"),
        ):
            result = sh.run(argv[2], check=False)
            assert result.returncode != 0
            assert b"not mounted" in result.stderr

    def test_traversal_is_refused_before_any_command_exists(self, nfs):
        with pytest.raises(ValidationError):
            nfs.sync_in_command("file://working/../../etc/", "/tmp/x")
        with pytest.raises(ValidationError):
            nfs.cli_copy_in("file://working/../x", "/tmp/x")

    def test_contents_copy(self, sh, nfs, tmp_path):
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        (outputs / "$(touch PWNED)").write_bytes(b"1")
        sh.run(nfs.cli_copy_out_contents(str(outputs), "file://results/projects/1/pipeline-runs/2/"))
        landed = nfs._path("file://results/projects/1/pipeline-runs/2/$(touch PWNED)")
        assert open(landed, "rb").read() == b"1"

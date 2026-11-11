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


class Shell:
    """Runs generated shell text with recording CLIs and no other side effects allowed."""

    def __init__(self, tmp_path):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.record = tmp_path / "argv"
        for tool in ("gcloud", "gsutil", "aws"):
            (self.bin / tool).write_text(RECORDER)
            (self.bin / tool).chmod(0o755)
        self.cwd = tmp_path / "cwd"
        self.cwd.mkdir()

    def run(self, script: str, *, check: bool = True) -> subprocess.CompletedProcess:
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "RECORD": str(self.record), "HOME": str(self.root)}
        result = subprocess.run(["/bin/sh", "-c", script], cwd=self.cwd, env=env, capture_output=True, timeout=10)
        assert os.listdir(self.cwd) == [], f"side effects: {os.listdir(self.cwd)}"
        if check:
            assert result.returncode == 0, result.stderr
        return result

    def calls(self) -> list[list[str]]:
        if not self.record.exists():
            return []
        lines = self.record.read_bytes().split(b"\0\n")
        return [line.decode().split("\0") for line in lines if line]


GCS_NEWLINE_EXCLUDE = "--exclude=(?s).*\\n.*"


def gcs_escape(name: str) -> str:
    return name.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


@pytest.fixture
def sh(tmp_path):
    return Shell(tmp_path)


class TestCloudCopyCommands:
    @pytest.mark.parametrize("name", HOSTILE_NAMES)
    def test_gcs_copy_in_passes_both_operands_literally(self, sh, name):
        uri, dest = f"gs://b/uploads/{name}", f"/data/x/{name}"
        sh.run(GcsStorageProvider().cli_copy_in(uri, dest))
        assert sh.calls() == [["storage", "cp", gcs_escape(uri), dest]]

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
            ["storage", "rsync", "--recursive", GCS_NEWLINE_EXCLUDE, str(outputs), "gs://b/run/"],
            ["s3", "cp", "--recursive", str(outputs), "s3://b/run/"],
        ]

    def test_gcs_contents_copy_takes_newline_names_one_file_at_a_time(self, sh, tmp_path):
        """`gcloud storage rsync` crashes on a newline in a local name (FileNotFoundError on the
        part before it, after uploading the rest), and a destination object may not contain
        `[*?`. So rsync skips every path with a newline, and each such file is copied by `cp`
        into its own directory prefix, where the basename is kept literally (checked against
        the fake GCS server, including a trailing newline and `[1]` with a newline)."""
        outputs = tmp_path / "outputs"
        (outputs / "sub [x]").mkdir(parents=True)
        (outputs / "d\nx").mkdir()
        files = {
            "with\nnewline.txt": "",
            "nl\n": "",
            "sub [x]/a[1]\nb.txt": "sub [x]/",
            "d\nx/f.txt": "d\nx/",
            "ok.txt": None,
        }
        for rel in files:
            (outputs / rel).write_text(rel)
        sh.run(GcsStorageProvider().cli_copy_out_contents(str(outputs), "gs://b/run"))
        rsync, *copies = sh.calls()
        assert rsync == ["storage", "rsync", "--recursive", GCS_NEWLINE_EXCLUDE, str(outputs), "gs://b/run/"]
        assert sorted(copies) == sorted(
            ["storage", "cp", gcs_escape(f"{outputs}/{rel}"), f"gs://b/run/{prefix}"]
            for rel, prefix in files.items()
            if prefix is not None
        )

    def test_gcs_contents_copy_attempts_every_newline_file_then_fails(self, sh, tmp_path):
        """One refused copy must not cost the others: every file is attempted, then the copy
        fails. (Real case: a newline file under a directory named with `[*?`, whose prefix
        `gcloud` refuses as a wildcard destination; see findings, owner review 2.)"""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        for n in ("a\nb", "c\nd"):
            (outputs / n).write_text("x")
        (sh.bin / "gcloud").write_text(RECORDER + '[ "$2" = cp ] && exit 1\nexit 0\n')
        result = sh.run(GcsStorageProvider().cli_copy_out_contents(str(outputs), "gs://b/run/"), check=False)
        assert result.returncode != 0
        assert len([c for c in sh.calls() if c[:2] == ["storage", "cp"]]) == 2

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

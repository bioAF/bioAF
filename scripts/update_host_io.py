"""Host-side file access for the update agent and ./bioaf at the backend boundary.

update-requests/ and update-status/ are mounted writable into the backend container, so every name,
payload and directory entry there is untrusted. This program is the one place the host reads a
trigger, publishes current.json and reads it back. The agent and ./bioaf load it into memory when
they start and run it as ``python3 -I -c "$source" <command> <repo> ...``; values travel as
arguments, never as generated code.

    read-trigger <repo> <name>          one line: "ok<TAB>version<TAB>upgrade_id" or "rejected<TAB>reason"
    write-status <repo> <key> <value>...  publish update-status/current.json (mode 0644)
    read-status <repo>                  the "status" of current.json; nothing unless it is a regular file
    record-agent-code <repo>            note which agent code is running (host-state/agent-code)
    agent-code-current <repo>           exit 0 when the recorded agent code matches the code on disk

Writes are staged in host-state/: a directory outside every backend mount, owned by this user and
closed to group and other. The temporary file is created exclusively there, completed, set to 0644,
and renamed over the entry in update-status/ (a leaf symlink there is replaced, never followed).
There is no fallback to staging in the shared directory and no copy across filesystems.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import sys

TRIGGER_DIR = "update-requests"
STATUS_DIR = "update-status"
STATE_DIR = "host-state"
STATUS_FILE = "current.json"
AGENT_CODE = "agent-code"
AGENT_SOURCES = ("scripts/update-agent.sh", "scripts/update_host_io.py")

TRIGGER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.json", re.ASCII)
VERSION = re.compile(r"v?[0-9]+\.[0-9]+\.[0-9]+", re.ASCII)
MAX_TRIGGER_BYTES = 64 * 1024
MAX_STATUS_BYTES = 1024 * 1024

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class Refused(Exception):
    """A fixed, printable reason: never contains untrusted input."""


def open_subdir(repo_fd: int, name: str, create: bool = False, private: bool = False) -> int:
    if create:
        try:
            os.mkdir(name, 0o700 if private else 0o755, dir_fd=repo_fd)
        except FileExistsError:
            pass
    try:
        fd = os.open(name, DIR_FLAGS, dir_fd=repo_fd)
    except OSError:
        raise Refused(f"{name}/ is missing, not a directory, or a symlink") from None
    if private:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or st.st_mode & 0o077:
            os.close(fd)
            raise Refused(f"{name}/ is not private to this user")
    return fd


def read_regular(dir_fd: int, name: str, limit: int) -> bytes:
    """Read one regular file opened without following links, checked on the same descriptor."""
    try:
        fd = os.open(name, READ_FLAGS, dir_fd=dir_fd)
    except OSError:
        raise Refused("unreadable, or a symlink") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Refused("not a regular file")
        if st.st_size > limit:
            raise Refused("too large")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise Refused("too large")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def parse_trigger(raw: bytes) -> tuple[str, str]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused("not JSON") from None
    if not isinstance(data, dict):
        raise Refused("not a JSON object")
    version = data.get("version")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise Refused("missing or invalid version")
    upgrade_id = data.get("upgrade_id")
    if upgrade_id is not None and (
        isinstance(upgrade_id, bool) or not isinstance(upgrade_id, int) or not 0 < upgrade_id < 2**63
    ):
        raise Refused("invalid upgrade_id")
    requested_at = data.get("requested_at")
    if requested_at is not None and not isinstance(requested_at, str):
        raise Refused("invalid requested_at")
    # Other keys are ignored: they are never logged or passed on.
    return version, "" if upgrade_id is None else str(upgrade_id)


def read_trigger(repo_fd: int, name: str) -> str:
    try:
        if not TRIGGER_NAME.fullmatch(name):
            raise Refused("unsupported file name")
        dir_fd = open_subdir(repo_fd, TRIGGER_DIR)
        try:
            version, upgrade_id = parse_trigger(read_regular(dir_fd, name, MAX_TRIGGER_BYTES))
        finally:
            os.close(dir_fd)
    except Refused as reason:
        return f"rejected\t{reason}"
    return f"ok\t{version}\t{upgrade_id}"


def publish(repo_fd: int, dest_dir: str, dest: str, content: bytes) -> None:
    """Stage content privately, set 0644, then rename it over dest_dir/dest."""
    state_fd = open_subdir(repo_fd, STATE_DIR, create=True, private=True)
    try:
        dest_fd = open_subdir(repo_fd, dest_dir, create=True)
        try:
            tmp = f".{dest}.{secrets.token_hex(8)}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=state_fd)
            published = False
            try:
                view = memoryview(content)
                while view:
                    view = view[os.write(fd, view) :]
                os.fsync(fd)
                os.fchmod(fd, 0o644)
                os.close(fd)
                fd = -1
                os.replace(tmp, dest, src_dir_fd=state_fd, dst_dir_fd=dest_fd)
                published = True
            finally:
                if fd >= 0:
                    os.close(fd)
                if not published:
                    try:
                        os.unlink(tmp, dir_fd=state_fd)
                    except OSError:
                        pass
        finally:
            os.close(dest_fd)
    finally:
        os.close(state_fd)


def write_status(repo_fd: int, pairs: list[str]) -> None:
    if len(pairs) % 2:
        raise Refused("write-status takes key value pairs")
    record = dict(zip(pairs[0::2], pairs[1::2]))
    publish(repo_fd, STATUS_DIR, STATUS_FILE, (json.dumps(record) + "\n").encode("utf-8"))


def read_status(repo_fd: int) -> str:
    try:
        dir_fd = open_subdir(repo_fd, STATUS_DIR)
        try:
            data = json.loads(read_regular(dir_fd, STATUS_FILE, MAX_STATUS_BYTES).decode("utf-8"))
        finally:
            os.close(dir_fd)
    except (Refused, UnicodeDecodeError, ValueError):
        return ""
    status = data.get("status") if isinstance(data, dict) else None
    return status if isinstance(status, str) and status.isidentifier() else ""


def agent_code(repo: str) -> str:
    digest = hashlib.sha256()
    for rel in AGENT_SOURCES:
        with open(os.path.join(repo, rel), "rb") as f:
            digest.update(hashlib.sha256(f.read()).digest())
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command, repo, rest = argv[0], argv[1], argv[2:]
    try:
        repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        print("The bioAF directory could not be opened.", file=sys.stderr)
        return 1
    try:
        if command == "read-trigger" and len(rest) == 1:
            print(read_trigger(repo_fd, rest[0]))
        elif command == "write-status":
            write_status(repo_fd, rest)
        elif command == "read-status":
            print(read_status(repo_fd))
        elif command == "record-agent-code":
            publish(repo_fd, STATE_DIR, AGENT_CODE, agent_code(repo).encode("ascii"))
        elif command == "agent-code-current":
            state_fd = open_subdir(repo_fd, STATE_DIR)
            try:
                recorded = read_regular(state_fd, AGENT_CODE, 128).decode("ascii", "replace")
            finally:
                os.close(state_fd)
            return 0 if recorded == agent_code(repo) else 1
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except Refused as reason:
        print(f"Refused: {reason}.", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Failed: {error.strerror or 'operating system error'}.", file=sys.stderr)
        return 1
    finally:
        os.close(repo_fd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

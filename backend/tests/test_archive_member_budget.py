"""plan_8_6 section 4: a member bioAF does not read is recorded as unread, never silently dropped.

`inspect_archive` says that in its own words and then took the first 400 MEMBERS of the archive,
whatever they were. Two ways that loses code without a word:

- a repository that ships its data beside its scripts spends the budget on files that are not code
  at all, and the script in position 401 is never seen;
- a repository with more than 400 code files keeps 400 of them and says nothing about the rest.

Either way the C obligations and M5.B read bioAF's incomplete inspection as the authors' missing
work, which is the reading section 4 exists to prevent. The budget bounds the SOURCE bioAF keeps,
and what it stops at is named.
"""

import io
import tarfile

from app.services.validation_code_inspection import MAX_SOURCES, inspect_archive


def _archive(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in members:
            info = tarfile.TarInfo(f"repo-abc123/{name}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class TestDataFilesDoNotCrowdOutTheCode:
    def test_a_script_behind_hundreds_of_data_files_is_still_read(self):
        members = [(f"data/matrix_{i}.csv", b"1,2,3\n") for i in range(MAX_SOURCES + 50)]
        members.append(("analysis/deg.py", b"fc_thresh = 1\n"))
        found = inspect_archive(_archive(members), origin="x")
        assert any(s["path"] == "analysis/deg.py" for s in found["sources"])


class TestMoreCodeThanBioafReadsIsNamed:
    def _found(self):
        members = [(f"src/step_{i:04d}.py", b"threshold = 1\n") for i in range(MAX_SOURCES + 5)]
        return inspect_archive(_archive(members), origin="x")

    def test_it_keeps_what_its_budget_allows(self):
        found = self._found()
        assert len(found["sources"]) == MAX_SOURCES

    def test_the_files_it_did_not_read_are_on_the_record(self):
        found = self._found()
        unread = [row for row in found["skipped"] if row["path"].startswith("src/step_")]
        read = {s["path"] for s in found["sources"]}
        assert len(unread) == 5
        assert all(row["path"] not in read for row in unread)
        assert all("did not read" in row["reason"] for row in unread)

    def test_the_code_checks_see_them_as_unread_code(self):
        """`unreadable` is the list the code checks read, and an unread script belongs in it."""
        found = self._found()
        assert len([row for row in found["unreadable"] if row["path"].startswith("src/step_")]) == 5

"""The benchmark database harness only ever tunes a server it owns.

Durability tuning (fsync, synchronous_commit, full_page_writes off) is for
throwaway benchmark servers. The harness provisions its own PostgreSQL with
initdb, verifies the effective settings from a fresh connection, and removes
the server afterwards. The only other server it will tune is the CI service
container, and only inside GitHub Actions.
"""

from __future__ import annotations

import json
import sys

import pytest

from tests.support import disposable_pg as dpg

needs_initdb = pytest.mark.skipif(dpg.find_pg_bin() is None, reason="PostgreSQL server binaries (initdb) not found")


@needs_initdb
@pytest.mark.parametrize("tuned", [False, True])
def test_a_provisioned_server_has_the_requested_settings_and_is_removed(tuned):
    with dpg.provision(tuned=tuned) as server:
        settings = dpg.read_settings(server.url)
        expected = dpg.TUNED if tuned else dpg.DEFAULTS
        assert {k: settings[k] for k in expected} == expected
        assert server.identity()["system_identifier"]
        data_dir = server.data_dir
        assert data_dir.exists()
    assert not data_dir.exists()


@needs_initdb
def test_run_records_the_instance_and_hands_its_url_to_the_command(tmp_path):
    record = tmp_path / "instance.json"
    probe = "import os; print(os.environ['BIOAF_TEST_DATABASE_URL'])"
    code = dpg.main(["run", "--tuned", f"--record={record}", "--", sys.executable, "-c", probe])
    assert code == 0
    data = json.loads(record.read_text())
    assert data["tuned"] is True
    assert data["settings"] == {**data["settings"], **dpg.TUNED}
    assert data["identity"]["port"]
    assert data["removed"] is True


def test_verify_settings_fails_on_a_mismatch():
    with pytest.raises(dpg.SettingsMismatch):
        dpg.verify(dict(dpg.DEFAULTS), dpg.TUNED)


def test_the_ci_service_is_only_tuned_inside_github_actions(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(dpg.Refused):
        dpg.tune_ci_service("postgresql+asyncpg://bioaf_test:test@localhost:5432/bioaf_test")


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://bioaf_app:devpassword@localhost:5432/bioaf",  # the development database
        "postgresql+asyncpg://bioaf_test:test@db.example.com:5432/bioaf_test",  # not the service container
        "postgresql+asyncpg://bioaf_test:test@localhost:6543/bioaf_test",
    ],
)
def test_the_ci_tuner_refuses_anything_but_the_service_container(monkeypatch, url):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(dpg.Refused):
        dpg.tune_ci_service(url)

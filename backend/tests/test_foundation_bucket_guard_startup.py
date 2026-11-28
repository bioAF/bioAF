"""The update itself enforces public-access prevention on the two GCP foundation buckets.

Bootstrap created the Terraform state and backup buckets outside Terraform's state, so no plan can
change them. The backend applies this one setting to the two recorded buckets when it starts on the
new release: no operator commands. Only the recorded names, only that field, read back after.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.platform.platform_config_service import PlatformConfigService
from app.services import foundation_bucket_guard

STATE = "bioaf-tfstate-bioaf-blackbird"
BACKUPS = "bioaf-backups-bioaf-blackbird"


async def _configure(session, **overrides):
    values = {
        "gcp_project_id": "bioaf-blackbird",
        "terraform_initialized": "true",
        "terraform_state_bucket": STATE,
        "backups_bucket_name": BACKUPS,
        **overrides,
    }
    for key, value in values.items():
        if value is not None:
            await PlatformConfigService.set(session, key, value)
    await session.commit()


class _Iam:
    def __init__(self, value):
        self.public_access_prevention = value


class _Bucket:
    def __init__(self, name, value, *, sticky=False, patch_error=None):
        self.name = name
        self.iam_configuration = _Iam(value)
        self.metageneration = 7
        self.sticky, self.patch_error = sticky, patch_error
        self.patched_with = None

    def reload(self):
        pass

    def patch(self, if_metageneration_match=None):
        if self.patch_error:
            raise self.patch_error
        self.patched_with = if_metageneration_match
        if self.sticky:
            self.iam_configuration.public_access_prevention = "inherited"


class _Client:
    def __init__(self, buckets):
        self.buckets = buckets
        self.asked = []

    def get_bucket(self, name):
        self.asked.append(name)
        if name not in self.buckets:
            raise LookupError(f"404 {name}")
        return self.buckets[name]


def _client(*buckets):
    return _Client({b.name: b for b in buckets})


@pytest.fixture
def gcp_live():
    """Run as a live (non-simulated) GCP installation."""
    with patch.object(foundation_bucket_guard, "_simulated", return_value=False):
        yield


@pytest.mark.asyncio
async def test_inherited_foundation_buckets_are_enforced_and_read_back(session, gcp_live):
    await _configure(session)
    state, backups = _Bucket(STATE, "inherited"), _Bucket(BACKUPS, "inherited")
    client = _client(state, backups)
    with patch.object(foundation_bucket_guard, "_storage_client", return_value=client):
        results = await foundation_bucket_guard.reconcile(session)
    assert results == {STATE: "enforced", BACKUPS: "enforced"}
    for bucket in (state, backups):
        assert bucket.iam_configuration.public_access_prevention == "enforced"
        # Only if the bucket has not changed since it was read.
        assert bucket.patched_with == 7
    assert client.asked == [STATE, BACKUPS]


@pytest.mark.asyncio
async def test_already_enforced_buckets_are_not_patched(session, gcp_live):
    await _configure(session)
    state, backups = _Bucket(STATE, "enforced"), _Bucket(BACKUPS, "enforced")
    with patch.object(foundation_bucket_guard, "_storage_client", return_value=_client(state, backups)):
        results = await foundation_bucket_guard.reconcile(session)
    assert results == {STATE: "already_enforced", BACKUPS: "already_enforced"}
    assert state.patched_with is None and backups.patched_with is None


@pytest.mark.asyncio
async def test_a_setting_that_does_not_read_back_is_reported_as_a_failure(session, gcp_live):
    await _configure(session)
    state, backups = _Bucket(STATE, "inherited", sticky=True), _Bucket(BACKUPS, "inherited")
    with patch.object(foundation_bucket_guard, "_storage_client", return_value=_client(state, backups)):
        results = await foundation_bucket_guard.reconcile(session)
    assert results[STATE].startswith("failed")
    # One bucket's failure does not stop the other.
    assert results[BACKUPS] == "enforced"


@pytest.mark.asyncio
async def test_an_api_error_on_one_bucket_is_reported_and_the_other_still_enforced(session, gcp_live):
    await _configure(session)
    state = _Bucket(STATE, "inherited", patch_error=RuntimeError("403 storage.buckets.update"))
    backups = _Bucket(BACKUPS, "inherited")
    with patch.object(foundation_bucket_guard, "_storage_client", return_value=_client(state, backups)):
        results = await foundation_bucket_guard.reconcile(session)
    assert results[STATE].startswith("failed") and "403" in results[STATE]
    assert results[BACKUPS] == "enforced"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"terraform_initialized": "false"},
        {"cloud_provider": "aws"},
        {"terraform_state_bucket": "", "backups_bucket_name": ""},
    ],
)
async def test_nothing_is_touched_unless_this_is_an_initialized_gcp_installation(session, gcp_live, overrides):
    await _configure(session, **overrides)
    client = _client(_Bucket(STATE, "inherited"), _Bucket(BACKUPS, "inherited"))
    with patch.object(foundation_bucket_guard, "_storage_client", return_value=client):
        results = await foundation_bucket_guard.reconcile(session)
    assert results == {}
    assert client.asked == []


@pytest.mark.asyncio
async def test_a_recorded_name_outside_bioaf_buckets_is_refused_not_touched(session, gcp_live):
    await _configure(session, backups_bucket_name="someone-elses-bucket")
    other = _Bucket("someone-elses-bucket", "inherited")
    client = _client(_Bucket(STATE, "inherited"), other)
    with patch.object(foundation_bucket_guard, "_storage_client", return_value=client):
        results = await foundation_bucket_guard.reconcile(session)
    assert results[STATE] == "enforced"
    assert results["someone-elses-bucket"].startswith("refused")
    assert "someone-elses-bucket" not in client.asked


@pytest.mark.asyncio
async def test_a_simulated_installation_makes_no_cloud_calls(session):
    await _configure(session)
    with (
        patch.object(foundation_bucket_guard, "_simulated", return_value=True),
        patch.object(foundation_bucket_guard, "_storage_client", side_effect=AssertionError("cloud call")),
    ):
        assert await foundation_bucket_guard.reconcile(session) == {}


def test_the_step_runs_at_startup_as_an_optional_step():
    from app.startup import NORMAL_STEPS

    step = next(s for s in NORMAL_STEPS if s.name == "foundation_bucket_guard")
    assert step.policy == "optional"

"""One UserSummary wire shape, importable from every schema module that offered one."""

import importlib
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

MODULES = [
    "app.schemas.environment",
    "app.schemas.experiment",
    "app.schemas.notebook_session",
    "app.schemas.pipeline_run",
    "app.schemas.reference_dataset",
    "app.schemas.work_node",
]


def _summary(module: str):
    return importlib.import_module(module).UserSummary


def test_every_module_offers_the_same_definition():
    assert len({_summary(m) for m in MODULES}) == 1


@pytest.mark.parametrize("module", MODULES)
def test_fields_and_defaults(module):
    cls = _summary(module)
    assert cls(id=1, email="a@b.test").model_dump() == {"id": 1, "name": None, "email": "a@b.test"}
    assert cls(id=1, name="Ann", email="a@b.test").model_dump() == {"id": 1, "name": "Ann", "email": "a@b.test"}
    with pytest.raises(ValidationError):
        cls(id=1, name="Ann")


@pytest.mark.parametrize("module", MODULES)
def test_validates_from_an_orm_style_object(module):
    user = SimpleNamespace(id=7, name=None, email="u@b.test", password_hash="secret")
    assert _summary(module).model_validate(user).model_dump() == {"id": 7, "name": None, "email": "u@b.test"}

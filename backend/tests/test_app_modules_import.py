"""Every module in the application package imports, and the ORM mappers configure with all of them loaded.

A module that cannot be imported, or a model whose relationships no longer match the live models, is
source nothing reaches: it breaks the first time anything imports it.
"""

import importlib
import pkgutil

from sqlalchemy.orm import configure_mappers

import app


def test_every_app_module_imports_and_the_mappers_configure():
    failures = []
    for info in pkgutil.walk_packages(app.__path__, "app."):
        if info.name.endswith(".__main__"):  # `python -m` entry points run their command on import
            continue
        try:
            importlib.import_module(info.name)
        except Exception as error:  # noqa: BLE001 - every failure is reported, not just the first
            failures.append(f"{info.name}: {type(error).__name__}: {error}")
    assert not failures, "\n".join(failures)
    configure_mappers()

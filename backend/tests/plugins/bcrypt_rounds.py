"""The test-only bcrypt cost switch.

conftest calls ``apply_test_bcrypt_rounds(os.environ)`` before any application
import, so ``Settings()`` reads the test cost. ``BIOAF_TEST_BCRYPT_ROUNDS``
selects it (default 4; set 12 for an unchanged-hashing baseline) and always
overwrites ``BIOAF_BCRYPT_ROUNDS``, so a developer's application setting cannot
change what the suite measures. Production code reads only
``BIOAF_BCRYPT_ROUNDS``, whose default stays 12.
"""

from __future__ import annotations

from collections.abc import MutableMapping

DEFAULT_TEST_ROUNDS = 4
# bcrypt accepts costs 4 through 31.
_MIN_ROUNDS, _MAX_ROUNDS = 4, 31


def apply_test_bcrypt_rounds(environ: MutableMapping[str, str]) -> int:
    raw = environ.get("BIOAF_TEST_BCRYPT_ROUNDS", str(DEFAULT_TEST_ROUNDS))
    try:
        rounds = int(raw)
    except ValueError:
        rounds = 0
    if not _MIN_ROUNDS <= rounds <= _MAX_ROUNDS:
        raise ValueError(f"BIOAF_TEST_BCRYPT_ROUNDS must be an integer from 4 to 31, got {raw!r}")
    environ["BIOAF_TEST_BCRYPT_ROUNDS"] = str(rounds)
    environ["BIOAF_BCRYPT_ROUNDS"] = str(rounds)
    return rounds

"""Validate the backend's settings without starting it: ``python -m app.cli.check_settings``.

The entrypoint runs it before the database upgrade and the server; ``./bioaf update`` and
``./bioaf migrate`` run it in a one-off container. It imports the one settings boundary
(``app.config``) and nothing that opens a database or provider connection.

Exit status: 0 valid, 78 (EX_CONFIG) settings rejected, 1 the check itself could not run. Messages
carry setting names and accepted values, never input values. Recovery steps belong to the caller.
"""

import importlib
import sys

EX_CONFIG = 78


def main() -> int:
    from app.settings_contract import SettingsError, is_simulated

    try:
        config = importlib.import_module("app.config")
    except SettingsError as exc:
        print(f"bioAF configuration error. {exc}", file=sys.stderr)
        print(
            "Correct the launcher configuration (the Compose env file or the container environment).",
            file=sys.stderr,
        )
        return EX_CONFIG
    except Exception as exc:
        print(f"bioAF could not check its settings ({type(exc).__name__}).", file=sys.stderr)
        return 1
    mode = config.settings.compute_mode
    simulation = "enabled" if is_simulated(mode) else "disabled"
    print(f"bioAF settings are valid: compute mode {mode.value} (simulation {simulation}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

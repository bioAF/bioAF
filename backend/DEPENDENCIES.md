# Backend dependencies

`pyproject.toml` is the only place backend dependencies are declared:

- `[project].dependencies`: what the application needs at runtime. Every third-party module imported by `app/` or
  `alembic/` is declared here directly, not left to arrive through another package
  (`tests/test_dependency_authority.py` checks this).
- `[dependency-groups].dev`: test and lint tools, and anything only the tests import.

`uv.lock` pins the exact versions (with artifact hashes) of both groups. Every installer uses it strictly:

| Consumer | Command |
| --- | --- |
| Backend image (`docker/Dockerfile.backend`) | `uv sync --locked --no-dev` into `/app/.venv`, first on `PATH` |
| CI lint and tests, benchmark workflow | `uv sync --locked` |
| Manual development | `uv sync --locked` |

`--locked` fails when the lock no longer matches `pyproject.toml`; it never rewrites the lock. `./bioaf build` and
`./bioaf update` need no uv on the host: the image build brings its own.

## uv version

uv **0.13.5** is used everywhere: the `COPY --from=ghcr.io/astral-sh/uv:...` line in `docker/Dockerfile.backend` and
the `astral-sh/setup-uv` steps in `.github/workflows/ci.yml`, `backend-benchmark.yml` and `release.yml`
(`tests/test_dependency_installers.py` holds them equal). Use the same version locally when changing the lock
(`uvx uv@0.13.5 lock ...`): another version may rewrite the lock's format without changing any selection.

To move to a newer uv, change all four places in one pull request, run `uv lock --check` with the new version, and
commit any format-only rewrite of `uv.lock` on its own so it is not mistaken for a dependency change.

## Changing dependencies

From `backend/`:

```bash
# Add or change a declaration, then lock without touching unrelated selections:
uv add 'somepackage>=1.2'              # runtime
uv add --group dev 'sometool>=3'       # development only

# Update one package (and only what it requires) within its declared range:
uv lock --upgrade-package somepackage

# Before pushing:
uv lock --check                         # the lock matches pyproject.toml
uv sync --locked                        # the environment matches the lock
```

Review the lock diff, not just the declaration: list every package whose version moved and why. `uv lock --upgrade`
(everything at once) is a deliberate, separate change, never part of another one.

`uv lock --check` proves only that the lock agrees with `pyproject.toml`. It says nothing about whether the
selections are current or free of known vulnerabilities.

### Floors, caps and pins

Security floors (`starlette`, `cryptography`, `pydantic-settings`, `python-multipart`, `PyJWT`, `pyasn1`), the
`boto3` and `kubernetes` major caps, and the exact `ruff` and `mypy` pins carry their reasons as comments in
`pyproject.toml`. Raise a floor when an advisory requires it; move a cap or pin only after the re-test its comment
describes. Never relax one to make a resolution succeed: diagnose the conflict instead.

## Automated updates

Dependabot (`.github/dependabot.yml`) opens weekly pull requests against `uv.lock` for the `/backend` project. It
leaves `ruff` and `mypy` alone and does not propose `kubernetes` or `boto3` major versions. Each pull request runs
the full CI gate; review it as above before merging. Repository-level security alerts and security update pull
requests are GitHub settings, not files in this repository.

## Releases

The release workflow changes only the project's version. Because `uv.lock` records that version, the workflow then
runs `uv lock` (without `--upgrade`) and `uv lock --check`, and commits the lock together with the version before
tagging. No third-party selection changes in a release commit; the image built from the tag installs that lock
strictly.

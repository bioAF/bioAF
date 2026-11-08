### Backend dependencies

- The backend's Python dependencies are now declared only in
  `backend/pyproject.toml` and pinned in `backend/uv.lock`. The backend image,
  CI and the benchmark workflow all install that lock with
  `uv sync --locked`, so the image ships exactly the versions CI tested.
  `requirements.txt` and `requirements-dev.txt` are gone.
- The image's locked environment is first on `PATH`: `./bioaf` commands from
  earlier releases (migrate, seed, register-outputs, rotate-encryption-keys)
  keep working unchanged, and `./bioaf build` and `./bioaf update` need no new
  tools on the host.
- Packages the backend imports directly (h5py, NumPy, botocore and others) are
  now declared instead of arriving through other packages.
- Each release commits the refreshed lock with its version, and Dependabot
  proposes backend dependency updates weekly.

### Fixes

- `alembic current` inside the backend container no longer fails with
  `KeyError: 'destination_rev'`.

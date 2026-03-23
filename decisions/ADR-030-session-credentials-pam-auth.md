# ADR-030: Per-User Session Credentials with PAM Authentication for Notebooks

**Status:** Accepted
**Date:** 2026-03-22
**Deciders:** Brent (repository owner)

---

## Context

bioAF deploys RStudio Server and JupyterHub as Kubernetes Pods on the `bioaf-interactive` node pool (ADR-021). The initial implementation launched RStudio with `--auth-none=1`, which disabled authentication entirely. Any user with the LoadBalancer URL could access the session without logging in. This was acceptable for early development but created two problems:

1. **No session isolation.** With auth disabled, any user who discovers the LoadBalancer IP can access any RStudio session. In a multi-user biotech environment, this means researchers could accidentally (or intentionally) access each other's notebooks and data.
2. **RStudio redirect loop.** RStudio Server's built-in auth-none mode interacted poorly with its secure cookie mechanism, causing redirect loops in some browser configurations. Disabling auth-none and using proper PAM login resolved the redirect issue as a side effect.

RStudio Server natively supports PAM (Pluggable Authentication Modules) for login. PAM authenticates against the Linux user database (`/etc/passwd`, `/etc/shadow`). If a Unix user exists in the container with a valid password, RStudio's default PAM configuration handles login with no additional setup.

The question was how to create those Unix users and passwords inside ephemeral Kubernetes Pods.

---

## Decision

Each bioAF user manages a single set of **session credentials** (username + password) through their profile page. These credentials are stored in PostgreSQL and injected into notebook Pods at launch time via the container startup script, which creates the Unix user and sets the password before starting RStudio Server.

### Credential Model

A `session_credentials` table with a one-to-one relationship to `users`:

| Column | Type | Purpose |
| --- | --- | --- |
| `id` | Integer PK | Auto-increment |
| `user_id` | Integer FK (unique) | One credential per user |
| `organization_id` | Integer FK | Scopes username uniqueness |
| `username` | String(64) | Unix username for PAM login |
| `password_hash` | String(255) | bcrypt hash |
| `created_at` | Timestamp | Creation time |
| `updated_at` | Timestamp | Last modification |

Unique constraint on `(organization_id, username)` prevents username collisions within an organization.

### Username Generation

- Auto-generated from email: strip domain, remove dots and special characters, lowercase. Example: `jane.doe@biotech.com` becomes `janedoe`.
- Collision resolution: append incrementing suffix (`janedoe2`, `janedoe3`).
- Users can override with a custom username (3-32 chars, lowercase alphanumeric + underscores, must start with a letter).

### Password Storage

Passwords are hashed with bcrypt via the same `AuthService.hash_password()` used for platform login passwords. The hash (not the plaintext) is passed to the K8s adapter for injection into the Pod.

### Pod Startup Script

User creation happens in the **main container's startup script**, not in an init container. This is critical: init containers have their own root filesystem snapshot, so any `/etc/passwd` or `/etc/shadow` entries created there are not visible to the main container.

The startup script for RStudio Pods:

```bash
useradd -m -d /home/jovyan -s /bin/bash <username> || true
echo '<username>:<bcrypt_hash>' | chpasswd -e
chown -R <username>:<username> /home/jovyan
exec /usr/lib/rstudio-server/bin/rserver \
  --www-address=0.0.0.0 --www-port=8787 --server-daemonize=0
```

The `chpasswd -e` flag accepts a pre-hashed password, so the plaintext never appears in the Pod spec. The `|| true` on `useradd` makes the script idempotent.

RStudio Pods run as root (`runAsUser: 0`) because RStudio Server needs to manage PAM sessions and write to `/var/run/rstudio`.

### Jupyter Sessions

JupyterHub sessions do not use session credentials. They launch with `--NotebookApp.token='' --NotebookApp.password=''`, which is acceptable because Jupyter sessions are already scoped to the launching user and do not expose a multi-user login page.

### API

- `GET /api/auth/me/session-credentials` -- returns credential status (configured/not, username, timestamps)
- `PUT /api/auth/me/session-credentials` -- create or update credentials (username optional, password required)

All credential changes are recorded in the audit log with the actor's user ID.

### Frontend

The profile page displays a collapsible session credentials section:

- **Configured:** blue banner with username and last-updated timestamp, "Change" button
- **Not configured:** red warning banner, "Set Up" button
- Launching an RStudio session without configured credentials returns an error

---

## Consequences

**Positive:**

- RStudio sessions are authenticated per-user with standard PAM login
- No plaintext passwords in Pod specs or environment variables -- only bcrypt hashes
- Eliminates the auth-none redirect loop
- Credentials are reusable across sessions -- users set them once
- Username uniqueness within an organization prevents collisions in shared-cluster scenarios

**Negative:**

- Users must configure session credentials before launching RStudio (one-time setup friction)
- The bcrypt hash is embedded in the Pod's startup command, which is visible in the Pod spec via `kubectl describe`. This is acceptable for the current threat model (cluster access is already restricted to admins) but could be tightened with Kubernetes Secrets in a future iteration
- RStudio Pods must run as root for PAM session management, which is a broader container permission than ideal

**Neutral:**

- Jupyter sessions remain unauthenticated (token-less), consistent with their single-user design
- The credential model supports future expansion to other authenticated notebook types without schema changes

---

## References

- ADR-021 (Kubernetes compute backend -- `bioaf-interactive` node pool)
- ADR-026 (SSH access -- kubectl exec for container debugging)
- ADR-009 (immutable audit log -- credential change logging)
- #156 (Session credentials implementation issue)
- #158 (Session credentials, user admin, and notification UX PR)

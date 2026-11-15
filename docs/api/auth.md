# Authentication and Authorization

## Identity model

External systems authenticate as a **service account**, which is just a
`User` row in bioAF with `is_service_account=true`. Service accounts:

- Cannot log in to the browser UI: login returns HTTP 401 with `Invalid email or password`.
  `service_account_login_blocked` is the audit reason, not the response detail.
- Hold exactly one **role**, set when the SA is created and changed only
  via Settings > Users & Accounts > Service Accounts.
- Own one or more **API Keys**. Keys carry their own scope list.

## Minting a key

1. In Settings > Users & Accounts > Service Accounts, click an SA and then
   **Mint key**.
2. Provide a name. The current UI derives scopes from the service account's role, so use a role limited
   to the public scope alphabet below. The mint API accepts an explicit `scopes` list for narrower keys.
   Effective access is capped by the service account's current role.
3. The full secret is shown exactly once: `biokey_<prefix>.<secret>`.
   Copy it. bioAF stores only a bcrypt hash.

## Sending the key

Pass the full token as a Bearer header on every request:

```http
Authorization: Bearer biokey_AbCdEfGhIjKlMnOp.<random-secret>
```

The Integration API requires an API key and rejects a browser-session JWT. The shared middleware also
recognizes API keys on internal routes: their permission checks intersect the role with the key's scopes.
Do not rely on blanket key rejection there. Internal routes are not a stable integration contract;
external clients should use `/api/v1/integrations`.

## Authorization (scope intersection)

Every request is authorized by intersecting the calling **key's scopes**
with the SA's **role permissions**:

```text
allowed = key.scopes & role.permissions
```

If the request's required scope (e.g. `projects:create`) is not in the
intersection, the request returns `403 Forbidden`.

A key cannot exceed either its stored scopes or its service account's current role. Changing the role can
remove access immediately or restore access within existing scopes; it does not add new scopes to an
existing key. Choose narrower scopes at mint time, or revoke and mint a replacement.

## Public scope alphabet

The scopes accepted at mint time on the public surface:

| Scope | Lets the key do |
| --- | --- |
| `projects:view` | GET `/projects`, `/projects/{id}`, `/projects/by-external/{external_id}` |
| `projects:create` | POST `/projects` |
| `projects:edit` | PATCH `/projects/{id}` |
| `experiments:view` | GET `/experiments`, `/experiments/{id}`, `/experiments/by-external/{external_id}` |
| `experiments:create` | POST `/experiments` |
| `experiments:edit` | PATCH `/experiments/{id}` |
| `samples:view` | GET `/samples`, `/samples/{id}`, `/samples/by-external/{external_id}` |
| `samples:create` | POST `/samples` |
| `samples:edit` | PATCH `/samples/{id}` |
| `files:view` | GET `/files`, `/files/{id}` |

Internal role permissions outside this alphabet (e.g. `pipelines:launch`)
cannot be granted to a key, even if the SA's role has them.

## Revoking a key

Revocation is immediate. From the SA detail modal, click **Revoke** on the
key row. Revoked keys return HTTP 401 on the next request and
are flagged `revoked` in the keys table. Use revoke + re-mint to rotate.

Disabling a service account revokes all of its keys.

## Audit trail

Audited API-key mutations record both `user_id` (the service account's user ID) and `api_key_id`.
The Settings > Users & Accounts > API Activity tab joins these so each
row shows the service account display name plus the key name that made
the call.

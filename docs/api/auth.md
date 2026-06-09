# Authentication and Authorization

## Identity model

External systems authenticate as a **service account**, which is just a
`User` row in bioAF with `is_service_account=true`. Service accounts:

- Cannot log in to the browser UI (login is rejected with
  `service_account_login_blocked`).
- Hold exactly one **role**, set when the SA is created and changed only
  via Settings > Users & Accounts > Service Accounts.
- Own one or more **API Keys**. Keys carry their own scope list.

## Minting a key

1. In Settings > Users & Accounts > Service Accounts, click an SA and then
   **Mint key**.
2. Provide a key name. Scopes are inherited from the SA's role; the key
   gets one scope string per `(resource, action)` permission on the role.
3. The full secret is shown exactly once: `biokey_<prefix>.<secret>`.
   Copy it. bioAF stores only a bcrypt hash.

## Sending the key

Pass the full token as a Bearer header on every request:

```http
Authorization: Bearer biokey_AbCdEfGhIjKlMnOp.<random-secret>
```

Internal API endpoints (under `/api/...`, excluding `/api/v1/integrations`)
expect a JWT, not a key, and will return 401 if presented with a key.
Conversely, the integration surface returns 401 if presented with a JWT.

## Authorization (scope intersection)

Every request is authorized by intersecting the calling **key's scopes**
with the SA's **role permissions**:

```text
allowed = key.scopes & role.permissions
```

If the request's required scope (e.g. `projects:create`) is not in the
intersection, the request returns `403 Forbidden`.

This means: shortening a key's scope list narrows what an integration can
do without changing the SA. Changing the SA's role can narrow or broaden
every key under that SA at once.

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

Internal role permissions outside this alphabet (e.g. `pipelines:run`)
cannot be granted to a key, even if the SA's role has them.

## Revoking a key

Revocation is immediate. From the SA detail modal, click **Revoke** on the
key row. Revoked keys return `401 invalid_api_key` on the next request and
are flagged `revoked` in the keys table. Use revoke + re-mint to rotate.

Disabling a service account revokes all of its keys.

## Audit trail

Every API-key authenticated request that mutates state writes an
`audit_log` row with both `user_id` (the SA's user id) and `api_key_id`.
The Settings > Users & Accounts > API Activity tab joins these so each
row shows the service account display name plus the key name that made
the call.

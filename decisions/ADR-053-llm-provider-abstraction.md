# ADR-053: LLM Provider Abstraction and Single-Active Configuration

**Status:** Accepted
**Date:** 2026-05-15
**Deciders:** Brent (repository owner)

---

## Context

Under [ADR-052](ADR-052-llm-integration-trust-boundary.md), bioAF supports four LLM back ends (OpenAI, Anthropic Claude, Google Gemini, Gemma 4) with exactly one active per org. Storing only the active provider would force admins to re-enter keys whenever they switch; storing them all without an active-singleton invariant would invite drift (which provider answered which card?). The implementation needs a stable interface that bioAF code calls regardless of which back end is active, a per-org credential storage scheme that survives provider switching, and a response contract that lets bioAF parse severity-coded cards without each provider's free-form variability leaking into the UI.

---

## Decision

Introduce a provider abstraction with a uniform interface and a per-org configuration table that holds up to four rows per org with a partial unique index enforcing single-active.

### Provider interface

Every provider client implements two async methods:

```python
async def list_models(api_key: str | None) -> list[str]
async def submit(prompt: str, payload: str, attachments: list[bytes], model: str, api_key: str | None) -> str
```

Returning, respectively, the available model identifiers and the raw response text. The hosted clients (`openai_client.py`, `anthropic_client.py`, `google_client.py`) hit the provider's HTTPS API. The Gemma client (`gemma_client.py`) talks to the in-cluster Nextflow/GKE pipeline path defined in [ADR-054](ADR-054-gemma-per-request-inference.md); its `submit` returns a job handle rather than a synchronous response, with the pipeline monitor delivering the final text later.

### Configuration storage

New table `llm_provider_config`, additive:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL` PK | |
| `organization_id` | `INTEGER` FK | Indexed. |
| `provider` | `VARCHAR(32)` | One of `openai`, `anthropic`, `google`, `gemma`. |
| `api_key` | `EncryptedString` NULL | Encrypted at rest; null for Gemma. |
| `api_key_prefix_last5` | `VARCHAR(5)` NULL | Last 5 chars of the secret, plaintext, for audit-row reference. Null for Gemma. |
| `model` | `VARCHAR(255)` NULL | Admin's chosen model identifier. |
| `is_active` | `BOOLEAN NOT NULL DEFAULT false` | |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | |
| `created_by_user_id`, `updated_by_user_id` | `INTEGER` FK | |

Indexes:

- `(organization_id, provider)` UNIQUE: one row per (org, provider).
- `(organization_id) WHERE is_active = true` UNIQUE PARTIAL: at most one active provider per org.

API keys (OpenAI, Anthropic, Google) join the credential-grade column class established in [ADR-047](ADR-047-data-at-rest-encryption.md) and use the `EncryptedString` `TypeDecorator`. Gemma has no key. Storing the last 5 plaintext characters separately is deliberate: it lets audit rows reference "which key" without ever decrypting the column, and the prefix alone cannot be used to authenticate to the provider.

### Model selection

Live fetch at admin config time: when the admin opens Settings > Integrations > LLMs, the backend calls each provider's `/models` endpoint with the saved key and returns the lists. A hardcoded fallback list per provider (`app/services/llm_provider_models.py`) is used when the live fetch fails (provider down, key invalid, network error). The fallback list is global to the deployment, not per-org; updating it is a release activity, not a per-org config knob.

The admin selects the active model; users do not pick at run time. The selected model is snapshot onto every `agent_review` and `agent_review_job` row at submission so that switching models later does not retroactively change "what model produced this card."

### Response contract

Every provider response must satisfy a single contract enforced by the prompt template. The response begins with a fenced JSON block carrying the parsed header, followed by a free-text body:

```text
````json
{
  "severity": "red" | "orange" | "green",
  "headline": "<one-sentence summary>",
  "flags": [{"title": "...", "body": "...", "severity": "red"|"orange"|"green"}],
  "evidence": ["..."]
}
````

<free-text body>
```

bioAF parses the first fenced JSON block. On parse failure or schema mismatch it sets `severity = unknown`, populates `headline` with a parse-failure marker, and persists the full raw response in `body`. The card renders with a `parse_failure` indicator. The audit row records `parse_failure: true` in `details_json`.

### Permissions

Two new resource:action permissions, per [ADR-032](ADR-032-custom-rbac.md):

- `llm_integration:configure` -- admin only at bootstrap. Required to read, write, enable, disable, or rotate provider configuration.
- `llm_integration:use` -- admin and comp_bio at bootstrap. Required to trigger reviews and dismiss cards. Admin can grant to custom roles.

The admin user's role gets both. The comp_bio role gets only `:use`. Bench and viewer roles get neither.

---

## Out of scope

- Per-user keys. Keys are per-org; the assumption is the admin uses an org-owned API key.
- Per-run model picker.
- Quota or rate-limit configuration.
- Cross-org key sharing.
- Multi-active providers. Considered and rejected; switching is cheap because keys persist across the toggle.

---

## Consequences

### Positive

- The interface is small enough that adding a fifth provider is a single new client module plus a fallback model list entry.
- Single-active is enforced at the DB level (partial unique index), not just by application code. A bug that tried to flip two rows active simultaneously fails at the transaction.
- Audit rows can reference "which key authenticated this call" via `api_key_prefix_last5` without storing the secret in plaintext or decrypting on read.

### Negative

- The partial unique index has to be created with raw SQL in the Alembic migration (Alembic's `create_index(unique=True)` does not emit the `WHERE` clause). Acceptable; we use raw SQL there.
- The fallback model list is a maintenance commitment per release. Acceptable; the list is short and provider model churn is in fact a real-world signal admins want.
- The response contract pushes complexity onto the prompt template (the model has to obey the schema). The parse-failure fallback ensures the card still renders something useful even when the model misbehaves.

---

## References

- [ADR-009](ADR-009-immutable-audit-log.md) -- audit-row pattern.
- [ADR-032](ADR-032-custom-rbac.md) -- new permissions; addendum lands with this ADR.
- [ADR-047](ADR-047-data-at-rest-encryption.md) -- encrypted credential columns; addendum lands with this ADR.
- [ADR-052](ADR-052-llm-integration-trust-boundary.md) -- parent trust-boundary ADR.
- Specs: `local/spec-llm-integration-config.md`, `local/spec-llm-integration-payload.md`.

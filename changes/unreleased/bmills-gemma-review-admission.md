### Fixes

- Refuse Agent Review requests when the active LLM provider cannot run reviews
  (self-hosted Gemma). The request now fails with an explanation instead of
  leaving a review card pending forever. The assistant's review tool follows
  the same rule.
- On startup, fail Gemma reviews that earlier releases accepted but never ran,
  so their cards show a clear failure and new reviews are no longer blocked.
- LLM settings no longer claim Agent Review still works when Gemma is the
  active provider.

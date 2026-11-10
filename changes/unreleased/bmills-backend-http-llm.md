### Literature Sources

- Literature Source settings now govern real outbound traffic. Every request
  to a source is paced at that source's rate, including both PubMed calls per
  search, retries, OpenAlex paper lookups, Europe PMC full text, and
  connection tests. A saved rate override changes that rate.
- Literature Searches, AI Literature Reviews, and connection tests share each
  source's request allowance. When a source answers "too many requests", every
  caller waits as long as the source asks before trying it again.
- Brief source outages (rate limiting, 502/503/504 errors, dropped
  connections) are retried up to three times within the search's time limit
  instead of failing the source at once.
- A search or review stops waiting on a source when its time limit runs out,
  and records that source as failed rather than hanging.
- An ordinary Literature Search runs only the sources that are enabled.
  Asking for a disabled source is refused.
- OpenAlex is always on. It cannot be turned off, and it is always used for
  searches, AI Literature Reviews, citation searches, paper research context,
  and open-access lookup.
- A rate override can now be cleared by sending `null`. Negative, fractional,
  and non-numeric overrides are rejected.
- Semantic Scholar's default rate with an API key is now 1 request per
  second, the provider's documented starting rate.

### AI providers

- A malformed, truncated, or content-filtered response from OpenAI,
  Anthropic, or Google is now reported as a provider error. Before, it could
  appear as an empty answer, fail with an unexpected server error, or pass a
  malformed tool request to the assistant.
- Anthropic and Google timeouts now show the assistant's "did not respond in
  time" message.
- Loading a provider's model list retries brief outages before falling back
  to the built-in list. Completions and assistant turns are still sent
  exactly once, so a failure never repeats a paid request.

### Pipelines and studies

- Refreshing the nf-core pipeline registry, fetching pipeline parameter and
  samplesheet schemas, and reading GEO and ENA study data now retry brief
  outages. Existing registry entries are still kept when a refresh fails.

### Slack notifications

- Slack delivery retries only temporary failures: rate limiting (waiting as
  long as Slack asks, up to a minute), Slack server errors, and dropped
  connections. Other Slack errors, such as an archived channel or a deleted
  incoming webhook, now fail at once instead of being retried.
- A post that may already have reached Slack is still retried, so a channel
  can occasionally see a duplicate notification.

### Security and diagnostics

- A failed Literature Source connection test no longer returns the source's
  API key or request URL in its message. It shows a fixed description: HTTP
  status, rate limited, timed out, unreachable, or unreadable response.
- A failed Slack incoming-webhook delivery no longer logs the webhook URL.
- Server logs no longer include AI provider response bodies or raw
  connection error text from the provider clients or hosted Agent Review.
  They record the provider, error type, status, and whether the request
  timed out.

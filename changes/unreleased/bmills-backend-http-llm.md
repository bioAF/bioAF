### Fixes

- A failed Literature Source connection test no longer returns the request URL
  or the source API key in its message; it reports a fixed diagnostic (HTTP
  status, rate limited, timed out, unreachable, or unreadable response).
- A failed Slack incoming-webhook delivery no longer logs the webhook URL; logs
  carry only the HTTP status or failure class.
- A malformed, truncated, or filtered response from OpenAI, Anthropic, or
  Google is now a provider error instead of an empty answer, an unhandled 500,
  or a tool call with non-object arguments. Model listing falls back to the
  built-in list for these responses too, and Anthropic and Google timeouts now
  reach the assistant's "did not respond in time" message.

### Literature Sources

- Literature Source settings now govern real outbound traffic. Every request
  (both PubMed calls per search, retries, OpenAlex paper lookups, Europe PMC
  full text, and connection tests) is paced at the source's rate, and a saved
  rate override changes that rate. Searches, AI Literature Reviews, and
  connection tests share each source's quota, and a rate-limit response from a
  source holds every caller back until the source allows requests again.
- Transient source failures (rate limiting, 502/503/504, dropped connections)
  are retried up to three times within the search's time budget.
- Clearing a rate override (sending `null`) now works; negative or non-integer
  overrides are rejected.
- An ordinary Literature Search runs only enabled sources. Asking for a
  disabled source, or searching with none enabled, is refused; OpenAlex paper
  context and open-access lookups are refused while OpenAlex is turned off.
- Semantic Scholar's default rate with an API key is now 1 request per second,
  the provider's documented starting rate.

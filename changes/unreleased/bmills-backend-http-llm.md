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

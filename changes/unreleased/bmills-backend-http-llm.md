### Fixes

- A failed Literature Source connection test no longer returns the request URL
  or the source API key in its message; it reports a fixed diagnostic (HTTP
  status, rate limited, timed out, unreachable, or unreadable response).
- A failed Slack incoming-webhook delivery no longer logs the webhook URL; logs
  carry only the HTTP status or failure class.

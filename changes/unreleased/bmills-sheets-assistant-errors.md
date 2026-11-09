### Assistant

- The assistant works with OpenAI models that support tool calling only
  through the Responses API (such as gpt-6.1-sol). Conversations continue
  across turns, page reloads, and reopened history.
- When the AI provider fails (rejected credentials or configuration, rate
  limiting, timeouts, outages, or an unusable reply), the chat shows a clear
  message saying what to do instead of "Unknown error", and your bioAF
  session stays signed in.
- After you confirm a plan, the assistant's next answers reflect what actually
  ran, including a launch the compute provider refused. Confirming one plan no
  longer marks other pending plans in the same chat as confirmed, and a plan
  runs at most once.
- Each confirmed step is kept as soon as it completes. If a later step fails,
  the plan card says the plan stopped partway and marks each step Done,
  Failed, or Not run. Confirm and Cancel are offered only while a plan is
  still pending, and reopened conversations show each plan's real state.
- When a confirmed step fails unexpectedly, the chat says which action failed
  without showing internal error details. Asking the assistant to add a sample
  with an ID that already exists in the experiment now says so plainly.

### Google Sheets import

- Reading a Google Sheet for column headers, sample preview, or sample import
  now gives a specific, plain message for each kind of problem: a spreadsheet
  or tab that cannot be opened, a sheet not shared with the reader account,
  the Sheets API not enabled, reader credentials that Google does not accept,
  rate limiting, timeouts, and Google outages.

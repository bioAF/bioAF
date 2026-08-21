### Fixes

- Stop reporting an unreviewed pipeline run as a failed request. Asking for a
  run's active review now answers with an empty review instead of a 404 when
  nobody has reviewed it yet, so opening the Review tab on a fresh run no longer
  logs a failure in the browser. A review read that genuinely fails still says
  so on screen, and still blocks filing a second review over an unseen one.

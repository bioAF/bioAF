### Pipeline runs

- A run nobody has reviewed yet is no longer treated as a failed request. Every
  run starts unreviewed and stays there until a reviewer files a verdict, but
  asking bioAF for that run's review came back as an error, so opening the
  Review tab on a fresh run recorded a failure in the browser every time and
  buried the failures that matter. bioAF now answers plainly that there is no
  review yet.
- If a run's review genuinely cannot be read, the Review tab says so rather than
  showing the run as one nobody has reviewed, and it still refuses to arm a
  second review over a verdict the reviewer cannot see.

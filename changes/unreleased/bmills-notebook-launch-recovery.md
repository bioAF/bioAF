### Fixes

- Notebook sessions no longer fail after five minutes while a new node is still starting or the notebook image
  is still downloading. A launch now stays "starting" until its notebook container is ready and has an
  address, fails early only for a definite error, and otherwise fails after 30 minutes with the last cause
  observed. A brief "Unknown" pod state is no longer treated as a failure.
- Stopping a notebook session can no longer be overwritten by a launch result that arrives later; a stopped
  session previously could show as failed five minutes after it was stopped.
- A notebook session that fails, stops or never starts no longer leaves its pod and LoadBalancer running.
  A background pass deletes them every minute, after checking that they belong to the session, and keeps
  retrying until they are confirmed gone. It also cleans up such leftovers from earlier sessions. A session
  that failed while running has its outputs saved first.

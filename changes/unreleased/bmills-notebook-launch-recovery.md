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
- Stopping a notebook session never deletes it while its work is unsaved: if saving the home directory or
  outputs fails, the session's pod is kept and the save is retried first. bioAF also checks that a pod belongs
  to the session before running any command in it, and refuses to start a session when it cannot confirm
  which cluster the session would run on.
- Stopping a notebook session now saves its home directory. The save previously failed silently on every stop
  because it ran in a container without storage access, so files outside the git repository were lost. The
  restored home is owned by the notebook user, and retrying a stop no longer registers the same outputs twice.

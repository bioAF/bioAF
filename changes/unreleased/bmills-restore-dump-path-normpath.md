### Internal

- Switch the database-restore dump path construction to the `os.path.normpath` + safe-root `startswith` check that CodeQL's path-injection rule explicitly recognises. Replaces the previous `os.path.basename` line, which turned out not to be on the rule's barrier list. No behavior change for any user.

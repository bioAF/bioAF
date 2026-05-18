### Security

- Harden the database restore endpoint against path-traversal: the restore request now rejects any filename that isn't a real `pgdump-<timestamp>.dump` produced by the backup service. No effect on legitimate restores from the UI.

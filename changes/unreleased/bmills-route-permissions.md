### Permissions and ownership

- Every route now has a recorded permission decision, checked against the live route table by `backend/tests/test_route_permission_contract.py`. A new route with no gate fails that test until someone decides what it needs.
- Reads that any signed-in caller could reach now need a grant: experiments, samples, projects, files, datasets, templates, batches, documents, plots, cellxgene, storage, and health. No built-in role loses a page; viewer and bench lose document download, storage, and health by direct API only. Custom roles and API keys lose any of these they hold no grant for.
- User, component, and bootstrap routes now honor API-key scopes, which they previously ignored.
- A record owned by another Organization answers 404, including users, roles, samples, batches, and snapshots. Single-Organization installs see no change.
- Snapshots are authorized per record: `experiments:view` or `projects:view`, depending on what the snapshot links to. The project timeline and both project snapshot counts now include snapshots from the project's experiments.
- Content tokens are bound to their resource type, ID, Organization, purpose, and expiry, and can no longer be used as a session. File bytes need `files:view`, or results view for a file a plot or QC report names.
- Audit log entries from an API-key request now name the key.
- Project and reference actions follow exact grants instead of role names, so custom roles see what they can do. Forms that pick a project say when a role may not list projects instead of showing an empty picker.

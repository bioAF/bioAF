### Permissions and ownership

- Every route now has a recorded permission decision, checked against the live route table by `backend/tests/test_route_permission_contract.py`. A new route with no gate fails that test until someone decides what it needs.
- Reads that any signed-in caller could reach now need a grant: experiments, samples, projects, files, datasets, templates, batches, documents, plots, cellxgene, storage, and health. No built-in role loses a page; viewer and bench lose document download, storage, and health by direct API only. Custom roles and API keys lose any of these they hold no grant for.
- User, component, and bootstrap routes now honor API-key scopes, which they previously ignored.
- A record owned by another Organization answers 404, including users, roles, samples, batches, and snapshots. Single-Organization installs see no change.
- Snapshots are authorized per record: `experiments:view` or `projects:view`, depending on what the snapshot links to. The project timeline and both project snapshot counts now include snapshots from the project's experiments.
- Content tokens are bound to their resource type, ID, Organization, purpose, and expiry, and can no longer be used as a session. File bytes need `files:view`, or results view for a file a plot or QC report names.
- Audit log entries from an API-key request now name the key.
- Project and reference actions follow exact grants instead of role names, so custom roles see what they can do.
- Forms that pick a project say when a role may not list projects, and keep what that role can still do: Associate Paper and custom pipeline launch list experiments without a project, deep links still select their experiment, uploads keep experiment, sample, and global scope, and Add to Project can still create a project. A list that failed to load stays visible as a failure, with Retry where the picker is inline.
- Add to Project in the dataset browser follows `projects:create` instead of the admin and comp_bio role names.
- Experiment pickers on Associate Paper, custom pipeline launch, and upload list every experiment instead of the first page.

### Fixes

- Add Samples on a project works again. It read a route that does not exist; it now reads a new `GET /api/samples` list, which needs `samples:view`.
- The project list counts experiments and samples the way project detail does, including samples added through `project_samples`.
- Invitations can be accepted. The email links to a new `/accept-invite` page, and bulk invites now send that email.
- Admins can activate an invited user from Settings > Users. A password reset still leaves an invited user invited.

### Security

- Keep Work Node detail and Stop inside the caller's organization. A node in
  another organization now answers "not found", exactly like a missing one, so
  its owner, SSH address, and instance details are no longer readable from
  outside the organization, and an administrator can no longer stop it.
- Listing everyone's Work Nodes and stopping another user's node now require
  the `users:deactivate` grant in an API key's scopes as well as in its role.
  A key without that scope sees and stops only its own nodes. Signed-in users
  are unaffected.

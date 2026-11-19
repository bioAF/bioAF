### Fixes

- Show the Work Nodes link in the Workbench menu only to users who can view
  Work Nodes. It previously followed notebook permission, so users with
  notebook access but not Work Nodes access (including the built-in Bench
  role) saw a link that sent them back to the dashboard, and custom roles with
  Work Nodes access but not notebook access had no link.

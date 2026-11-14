### Security

- Update Next.js to 15.5.29 and the frontend's DOMPurify, sharp, fflate, source-map-js and Handlebars to their
  patched releases, closing the critical and high advisories reported against the frontend.
- Turn off Next.js's unused server-side image optimizer and leave its native image library out of the frontend
  image, which is about a third smaller.
- Keep local environment files, Terraform state, database dumps and build caches out of image builds.
- Database backup and restore failures now show a reference to look up in the server log instead of the
  database tool's raw output, which could contain research data. Only one backup and one restore run at a time,
  a backup never replaces an existing one of the same name, and dump files are private and removed when the
  operation ends.
- Database backups record a checksum when they are made, and a restore checks it, the backup's size, free disk
  space and archive readability before touching any database. A restore now succeeds or fails as a whole: an
  import error no longer opens a review of a partly restored copy. Backups made before this release are listed as
  unverified and need an explicit, audited confirmation to restore.

### Fixes

- Settings > Users shows Lock, Deactivate and Reactivate only to roles with the users:deactivate permission, and
  Delete only with users:delete, and checks again when the action is confirmed (#107).
- A notebook launched in a project now records that project, and one launched for an experiment records the
  experiment's project, so its outputs and Recreate keep it (#103). Notebook and work-node launches refuse a
  project, experiment or template version from another organization, or an experiment outside the chosen
  project, before anything is created.
- Experiment pickers (AI Lit Review, Library filter, work-node and notebook launch, Data & Files filter and
  Associate dialog, Plot Archive, glossary scans) now search by name and page through every experiment instead of
  stopping at the first 100 or 500 (#104, #105). Deleted experiments no longer appear in experiment lists unless
  the deleted status is chosen.

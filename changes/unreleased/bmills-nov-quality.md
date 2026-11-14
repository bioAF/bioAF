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

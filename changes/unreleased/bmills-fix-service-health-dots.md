### Fixes

- The Service Health widget on the Dashboard shows its status dots again. A
  narrowed Tailwind config was scanning only `app/` and `components/`, so the
  green (healthy) and yellow (degraded) dot colors, which live only in the shared
  status-style module under `lib/`, were stripped from the built CSS and rendered
  invisible. Tailwind now scans all of `src/`, so all health dots appear.

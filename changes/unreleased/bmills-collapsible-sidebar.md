### Navigation

- The left navigation can now be collapsed to a slim rail and re-expanded
  from a toggle in its header. The collapsed state persists across reloads
  in `localStorage` under `bioaf-sidebar-collapsed`.
- Only one section in the sidebar can be expanded at a time. Expanding a
  second section automatically collapses the first, and the auto-expand
  on navigation follows the same rule.
- The sidebar brand block now matches the main top bar's height so the two
  align on a single baseline.
- Adds the bioAF logo to the sidebar header next to the "bioAF" wordmark
  (and on its own when the sidebar is collapsed), with the "Comp Bio
  Automation Framework" tagline beneath the wordmark on a single line.

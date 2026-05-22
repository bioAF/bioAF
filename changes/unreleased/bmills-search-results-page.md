### Search

- Add a full search results page at `/search`, reached by pressing Enter in the
  global search bar instead of picking one of the dropdown hits. It searches
  experiments, samples, pipeline runs, files, projects, pipeline definitions,
  and literature papers, matching both names and content (descriptions,
  abstracts, and the like), and hides any type you do not have permission to
  view.
- Results are a single relevance-ranked list of cards, each tagged with its
  type and a context line that tells similar-looking results apart (for
  example, four files with the same name by the run that generated them and
  their sample). A type filter narrows results to one kind and shows a per-type
  count.
- Experiment result cards show the parent project, the number of samples,
  files, and pipeline runs, and the last activity date.
- Clicking a result takes you straight to the item.

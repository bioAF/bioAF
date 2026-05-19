### Literature

- New Literature Library under Data & Files. Upload PDFs and bioAF
  extracts the title, authors, DOI, and abstract automatically. Add
  threaded comments per paper, track your own reading status, and
  associate papers with experiments or projects so they show up where
  your work lives.
- Search across PubMed, bioRxiv, Europe PMC, and Semantic Scholar from
  one place. Results stay outside the Library until you tick the ones
  you want and click "Add to Library". Inline HTML tags and entities in
  titles and abstracts are cleaned on ingestion so display is plain
  text. Dedup by DOI prevents the same paper appearing twice.
- New "Lit Review" job on experiments: the platform asks your active
  LLM to generate adjacent search queries, pulls candidate papers from
  every enabled source, then scores them for relevance. Top picks are
  added straight to the Library, associated with the source experiment,
  and tagged with an "AI Lit Review Bot" note on the paper detail
  explaining why each was recommended.
- Library filters now use independent toggles for Active / Dismissed /
  Read / Unread / Reading plus project and experiment association
  pickers. Each row shows multiple status flags at once (e.g. dismissed
  and read together). Multi-select + bulk Associate works inline.
- Agent Review now reads from the library too. Abstracts and team
  comments on papers associated with the experiment can be bundled
  into the Agent Review prompt; admins control which inputs are on
  per org, project, or experiment, and can set a token budget so
  large libraries do not blow up the review.
- Export citations as BibTeX or RIS from any paper detail page; bulk
  export covers all papers in the current filter or scope.

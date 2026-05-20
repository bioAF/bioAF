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
- Read uploaded PDFs in the app: the paper detail page now has a
  paginated reader that shows one page at a time with Prev / Next and a
  page counter, plus a Download link. Your reading status advances as you
  read: reaching the second page marks a paper Reading and reaching the
  last page marks it Read (it only ever moves forward, so it never undoes
  a status you set by hand).
- Delete a paper from the Library (admin / comp_bio): this removes the
  uploaded PDF and any stored files from cloud storage to free space, and
  dismisses the paper so it leaves your active Library and future AI
  Literature Review. Its abstract, metadata, comments, and history are
  kept, and an admin can reverse the dismissal later (the PDF would need
  to be uploaded again).

### Fixes

- Infrastructure > Components now lists every provisioned GCS bucket. The
  references and literature buckets were missing because the metrics read
  path queried an incomplete set of bucket-name keys; it now derives the
  keys from the same source the listing iterates, so the view reflects
  what is actually deployed.
- The paper-detail PDF reader no longer compresses pages vertically. The
  page now keeps its true aspect ratio instead of being squished to fit
  the viewer height.

### Literature Library

- Add OpenAlex as a literature source, enabled for every organization (new and
  existing). It works without an API key; an admin can add a free key on the
  Sources page to raise OpenAlex's limits. A rate-limited or misconfigured
  OpenAlex search now shows as failed instead of returning zero papers.
- Add a Research context card to each paper, read live from OpenAlex: whether
  the paper is known to be retracted, its citation count, its available yearly
  citation history, and its open-access copies with each copy's version
  (submitted, accepted or published) and license. Unknown values are shown as
  unknown, not as zero or "not retracted", and the card notes that citations do
  not establish validity and that no retraction flag does not guarantee a clean
  record. If OpenAlex cannot be reached, only this card says so.
- Add "Papers this builds on" and "Papers citing this" to the Research context
  card. Each starts an OpenAlex search of the paper's references or citing
  papers and opens Searches, where results can be added to the library,
  associated with projects, or dismissed like any other search result.
- Add "Get open-access copy" to the Full paper card for papers with no PDF.
  bioAF downloads the best openly available PDF that OpenAlex lists (published
  version first, at most three tries within one minute), attaches it, and
  extracts its text so literature reviews can use the full paper. The card then
  shows which version was attached. Only a DOI printed on the copy's first page
  counts as its own, so a paper that merely cites this one is not mistaken for
  it. A copy that appears to be a different paper, whose first page names
  several DOIs, or whose DOI belongs to another paper in the library, is not
  attached and is never merged automatically. A copy that could not be saved
  is not attached, and the paper is left as it was.

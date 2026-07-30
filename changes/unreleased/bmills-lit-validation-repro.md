### Literature validation (Level 3)

- Add a `partially_reproduced` classification, surfaced as "Partially Reproduced", for a paper whose
  differential finding reproduced in part: the overlap with the paper's own result set is
  statistically real (enrichment clears) but directional recovery is below the agreement threshold.
  It always holds for a human, and it narrows `inconclusive` back to "we genuinely cannot tell"
  rather than lumping in "we clearly reproduced part of it."
- Support matched-pairs (paired / blocked) designs in the differential-expression reproduction.
  A per-sample subject/donor label captured at the reproduction gate makes the analysis run
  `~ subject + condition` (instead of the unpaired `~ condition`), cancelling donor-to-donor
  baseline variance and sharply raising power for paired studies. A confounded or unbalanced pairing
  is rejected at the gate before any compute spend.
- Auto-retry a transient data-acquisition failure (an ENA/SRA outage, connection-refused, or timeout)
  with bounded exponential backoff instead of parking the study in a terminal error that needs manual
  intervention. A genuinely unavailable accession is still classified `missing_data` immediately.

# Study 65: eLife 83291 (granulosa-like cells)

The owner's negative control. A known-bad paper that bioAF scored 27.5/100 positive and
**0/100 negative** on 2026-09-20, which is what plan_8_6 was written from.

## What is here

| File | What it is |
|---|---|
| `fulltext_jats.xml` | Europe PMC's full-text JATS for PMC9943069, fetched 2026-09-20. 98,246 characters of body text, 17 methods subsections, 7 figure legends, and the data-availability statement naming the repository and its archived revision. |

## Why it is committed

plan_8_6 section 10: each defect is accepted on the evidence it was found in, not on a unit
test alone. This is that evidence for defects 1, 4, 5 and 6:

- the **detailed RNA-seq methods** state `log 2 fc >3, p adj < 0.05` for the GO-enrichment
  input, which is the paper's half of the code/methods discrepancy;
- the **single-cell methods** state `scanpy ingest` atlas mapping, `Scanpy (version 1.8.2)`,
  doublet filtering and the Parse pipeline version, none of which reached the assessor;
- the **cell culture** methods are Matrigel, mTeSR Plus and EDTA passaging, which is what
  M1.B was wrongly verified from;
- the **legends and discussion** carry the replication facts ("Two biological replicates were
  collected for each sample", "6 ovaroids per sample, 2 samples per time point", selected
  high-performing clones), which E2.B needs and was not given;
- the **data availability** statement names `github.com/programmablebio/granulosa` and the
  cited revision `swh:1:rev:3c650290779db376c4d1f3a14960b08b17ae5561`.

Nothing here is fetched at test time.

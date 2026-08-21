### Files

- Deleting a file now frees the storage it occupied. Until now deletion removed
  the file from bioAF and left every byte in the bucket, so a scientist who
  deleted a 40 GB BAM to reclaim space reclaimed nothing and went on paying for
  it. bioAF still keeps a permanent record that the file existed, who deleted it
  and when, and which runs used it, so no result loses its provenance.
- The confirmation before a delete says what actually happens: the file is
  erased permanently, and neither it nor anything that still needs it can be
  recovered.
- A file whose storage cannot be reached is not deleted at all, rather than
  vanishing from bioAF while its data survives. Deleting several at once reports
  how many were erased instead of claiming that none were.

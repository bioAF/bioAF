### Reference data

- The URL importer now retries up to 3 times on a transient mid-stream
  network failure (peer hanging up the TCP connection, read timeout,
  remote-protocol error) with 5s / 10s / 20s backoff before declaring
  the import failed. Multi-gigabyte downloads from public CDNs (10x
  Genomics, Ensembl FTP, etc.) routinely lose a connection partway
  through; previously a single drop at 7 GB of an 11 GB body would mark
  the whole reference as failed and force a manual re-import. The retry
  emits a `downloading` progress event with `progress_pct=0` so the UI
  snaps back to zero on each new attempt instead of appearing stuck at
  the byte count from the dropped attempt.

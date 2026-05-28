### Notebook sessions

- Add larger notebook resource profiles for real single-cell work. The ladder
  is now small (2 CPU / 8 GB), medium (4 CPU / 16 GB), large (8 CPU / 32 GB),
  xlarge (16 CPU / 64 GB), and 2xlarge (16 CPU / 128 GB). The previous options
  topped out at 8 CPU / 16 GB, too small for Seurat/scanpy integration.

### Notebook environments

- Expand the default notebook image with the packages a bioinformatician
  actually needs: single-cell (scanpy, anndata, scvi-tools, harmonypy,
  scrublet, doubletdetection, celltypist, decoupler, muon, scVelo; Seurat with
  hdf5r, harmony, and presto), bulk RNA-seq differential expression (DESeq2,
  edgeR, limma), and enrichment/annotation (clusterProfiler, fgsea, SingleR,
  org.Hs.eg.db, org.Mm.eg.db).

### Work nodes

- Fix work nodes getting stuck in "Starting" even after the VM is up and SSH
  reachable. The launch request now commits the "starting" status before
  creating the VM and no longer rewrites status/access_url afterward, so the
  background readiness poll's "running" transition (and SSH URL) is no longer
  clobbered by the launch request's own commit.
- Make the work-node boot disk size and type configurable from Workbench
  Settings (default 100 GB pd-ssd, down from a hardcoded 200 GB). pd-ssd and
  pd-balanced both count toward the regional SSD_TOTAL_GB quota, so a large
  default could exhaust it and fail launches with a quota error; operators can
  now shrink the disk or switch to pd-standard (which uses the separate HDD
  quota) per their workload.
- Make multi-zone failover actually work when a zone is out of capacity.
  `instances.insert` is asynchronous, so a `ZONE_RESOURCE_POOL_EXHAUSTED`
  stockout only surfaced when the operation was resolved, which the launcher
  never did. As a result the failover loop broke on the first zone, the launch
  silently never created a VM, and it failed five minutes later with a
  misleading "no external IP" error. The launcher now resolves the insert
  operation so a stockout advances to the next zone in the region.

### Fixes

- Stop the default notebook image from silently shipping without R packages
  such as Seurat. `install.packages()` returns success even when a package
  fails to build, so the image now installs R packages as precompiled binaries
  from the Posit Public Package Manager and fails the build if any expected
  package is missing.

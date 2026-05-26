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

### Fixes

- Stop the default notebook image from silently shipping without R packages
  such as Seurat. `install.packages()` returns success even when a package
  fails to build, so the image now installs R packages as precompiled binaries
  from the Posit Public Package Manager and fails the build if any expected
  package is missing.

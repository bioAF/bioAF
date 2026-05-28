### Notebooks

- Rebuild the Default Notebook environment as a Conda specification. It had
  drifted to a hand-maintained Dockerfile whose image build failed, and it now
  ships the full single-cell stack again: scanpy, anndata, scvi-tools,
  harmonypy, scrublet, and gseapy on the Python side, plus Seurat and the core
  Bioconductor packages (SingleCellExperiment, scran, DESeq2, edgeR, limma,
  clusterProfiler, fgsea, ComplexHeatmap, and friends) reachable from R through
  the Jupyter R kernel.

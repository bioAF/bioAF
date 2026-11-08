\name{extract_seurat_metadata}
\alias{extract_seurat_metadata}
\title{Extract metadata from a Seurat object}
\usage{
extract_seurat_metadata(obj)
}
\arguments{
\item{obj}{A Seurat object}
}
\value{
A list of metadata suitable for snapshot submission
}
\description{
Extracts cell/gene counts, embeddings, clusterings, command log,
layers, and metadata columns from a Seurat object.
}

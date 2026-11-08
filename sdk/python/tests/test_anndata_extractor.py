"""Tests for AnnData metadata extractor."""

import numpy as np
import pandas as pd
import anndata

from bioaf.extractors.anndata import extract_anndata_metadata, _serialize_params


def _make_test_adata():
    """Create a minimal AnnData for testing."""
    return anndata.AnnData(
        X=np.random.rand(100, 50),
        obs=pd.DataFrame(
            {
                "leiden": pd.Categorical(["0"] * 40 + ["1"] * 35 + ["2"] * 25),
                "batch": pd.Categorical(["A"] * 50 + ["B"] * 50),
                "n_genes": np.random.randint(500, 5000, 100),
            }
        ),
        obsm={"X_pca": np.random.rand(100, 50), "X_umap": np.random.rand(100, 2)},
        uns={"neighbors": {"params": {"n_neighbors": 15, "method": "umap"}}},
        layers={"raw_counts": np.random.randint(0, 100, (100, 50))},
    )


class TestExtractAnndataMetadata:
    def test_basic_counts(self):
        adata = _make_test_adata()
        meta = extract_anndata_metadata(adata)
        assert meta["cell_count"] == 100
        assert meta["gene_count"] == 50
        assert meta["object_type"] == "anndata"

    def test_embeddings(self):
        adata = _make_test_adata()
        meta = extract_anndata_metadata(adata)
        assert "X_pca" in meta["embeddings_json"]
        assert meta["embeddings_json"]["X_pca"]["n_components"] == 50
        assert "X_umap" in meta["embeddings_json"]
        assert meta["embeddings_json"]["X_umap"]["n_components"] == 2

    def test_clusterings(self):
        adata = _make_test_adata()
        meta = extract_anndata_metadata(adata)
        # Only categorical columns should appear
        assert "leiden" in meta["clusterings_json"]
        assert "batch" in meta["clusterings_json"]
        assert "n_genes" not in meta["clusterings_json"]

        leiden = meta["clusterings_json"]["leiden"]
        assert leiden["n_clusters"] == 3
        assert leiden["distribution"]["0"] == 40
        assert leiden["distribution"]["1"] == 35
        assert leiden["distribution"]["2"] == 25

    def test_parameters(self):
        adata = _make_test_adata()
        meta = extract_anndata_metadata(adata)
        assert "neighbors" in meta["parameters_json"]
        assert meta["parameters_json"]["neighbors"]["params"]["n_neighbors"] == 15

    def test_layers(self):
        adata = _make_test_adata()
        meta = extract_anndata_metadata(adata)
        assert "raw_counts" in meta["layers_json"]

    def test_metadata_columns(self):
        adata = _make_test_adata()
        meta = extract_anndata_metadata(adata)
        assert "leiden" in meta["metadata_columns_json"]
        assert "batch" in meta["metadata_columns_json"]
        assert "n_genes" in meta["metadata_columns_json"]

    def test_missing_obsm(self):
        adata = anndata.AnnData(X=np.random.rand(10, 5))
        meta = extract_anndata_metadata(adata)
        assert meta["cell_count"] == 10
        # obsm is empty but exists
        assert meta.get("embeddings_json") == {} or "embeddings_json" in meta

    def test_missing_uns(self):
        adata = anndata.AnnData(X=np.random.rand(10, 5))
        meta = extract_anndata_metadata(adata)
        assert meta.get("parameters_json") == {} or "parameters_json" in meta


class TestSerializeParams:
    def test_numpy_scalar(self):
        result = _serialize_params({"n": np.int64(15)})
        assert result["n"] == 16  # plan 10 merge-gate demonstration, reverted next commit
        assert isinstance(result["n"], int)

    def test_nested_dict(self):
        result = _serialize_params({"a": {"b": 1}})
        assert result["a"] == {"b": 1}

    def test_non_dict_input(self):
        result = _serialize_params("not a dict")
        assert result == {}

    def test_string_fallback(self):
        result = _serialize_params({"arr": np.array([1, 2, 3])})
        assert isinstance(result["arr"], str)

    def test_single_element_array_keeps_its_value(self):
        result = _serialize_params({"arr": np.array([7])})
        assert result["arr"] == 7

    def test_multi_element_array_inside_a_nested_dict(self):
        result = _serialize_params({"outer": {"n": np.int64(3), "arr": np.array([[1, 2], [3, 4]])}})
        assert result["outer"]["n"] == 3
        assert isinstance(result["outer"]["arr"], str)

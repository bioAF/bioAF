"""Unit tests for the QC template registry.

Templates live in app.services.qc.templates. Each template owns its own
extractor, quality-rating logic, and render config. The dashboard service
dispatches by `qc_template` string -> Template instance.
"""


def test_templates_registry_has_scrnaseq_bulk_rnaseq_custom():
    from app.services.qc.templates import TEMPLATES

    assert "scrnaseq" in TEMPLATES
    assert "bulk_rnaseq" in TEMPLATES
    assert "custom" in TEMPLATES


def test_scrnaseq_render_config_shape():
    from app.services.qc.templates import TEMPLATES

    cfg = TEMPLATES["scrnaseq"].render_config()
    assert cfg["template"] == "scrnaseq"
    # Core sections include hero + cells + sequencing + mapping
    section_ids = {s["id"] for s in cfg["sections"]}
    assert "hero" in section_ids
    assert "cells" in section_ids
    # Hero section must surface cell_count + key per-cell metrics
    hero = next(s for s in cfg["sections"] if s["id"] == "hero")
    assert "cell_count" in hero["metrics"]
    # Metric definitions include label and format
    assert cfg["metrics"]["cell_count"]["label"] == "Estimated Number of Cells"
    assert cfg["metrics"]["cell_count"]["format"] == "integer"
    assert cfg["metrics"]["saturation"]["format"] == "percent_decimal"
    # Charts list includes barcode rank
    chart_types = [c["type"] for c in cfg["charts"]]
    assert "barcode_rank" in chart_types


def test_scrnaseq_compute_quality_matches_existing_rules():
    from app.services.qc.templates import TEMPLATES

    metrics = {
        "cell_count": 10000,
        "median_genes_per_cell": 3000,
        "median_reads_per_cell": 25000,
        "mito_pct_median": 2.0,
        "saturation": 0.85,
    }
    assert TEMPLATES["scrnaseq"].compute_quality(metrics) == "excellent"

    high_mito = {**metrics, "mito_pct_median": 25.0}
    assert TEMPLATES["scrnaseq"].compute_quality(high_mito) == "concerning"


def test_bulk_rnaseq_render_config_has_expected_metrics():
    from app.services.qc.templates import TEMPLATES

    cfg = TEMPLATES["bulk_rnaseq"].render_config()
    assert cfg["template"] == "bulk_rnaseq"
    metric_keys = set(cfg["metrics"].keys())
    assert "total_sequences" in metric_keys
    assert "percent_duplicates" in metric_keys
    assert "reads_mapped_genome" in metric_keys
    assert cfg["metrics"]["reads_mapped_genome"]["format"] == "percent_decimal"
    section_ids = {s["id"] for s in cfg["sections"]}
    assert "hero" in section_ids
    assert "mapping" in section_ids


def test_custom_render_config_uses_pipeline_override():
    """Custom template defers its render config to the pipeline-version
    override. Without an override it returns a minimal placeholder."""
    from app.services.qc.templates import TEMPLATES

    override = {
        "template": "custom",
        "sections": [{"id": "main", "layout": "grid", "metrics": ["foo"]}],
        "metrics": {"foo": {"label": "Foo Score", "format": "decimal"}},
        "charts": [],
        "plots": [],
    }
    cfg = TEMPLATES["custom"].render_config(override=override)
    assert cfg["sections"][0]["metrics"] == ["foo"]
    assert cfg["metrics"]["foo"]["label"] == "Foo Score"

    # No override -> empty / placeholder shape, but still valid
    bare = TEMPLATES["custom"].render_config()
    assert bare["template"] == "custom"
    assert isinstance(bare["sections"], list)
    assert isinstance(bare["metrics"], dict)


def test_custom_quality_defaults_to_pending_review_unless_emitted():
    from app.services.qc.templates import TEMPLATES

    assert TEMPLATES["custom"].compute_quality({}) == "pending_review"
    # If pipeline emits an explicit rating, honor it
    assert TEMPLATES["custom"].compute_quality({"quality_rating": "good"}) == "good"


def test_bulk_rnaseq_compute_quality_basic_thresholds():
    from app.services.qc.templates import TEMPLATES

    good = {
        "total_sequences": 100_000_000,
        "percent_duplicates": 12.0,
        "percent_gc": 48.0,
        "reads_mapped_genome": 0.93,
    }
    assert TEMPLATES["bulk_rnaseq"].compute_quality(good) == "good"

    poor = {
        "total_sequences": 50_000_000,
        "percent_duplicates": 60.0,
        "reads_mapped_genome": 0.3,
    }
    assert TEMPLATES["bulk_rnaseq"].compute_quality(poor) == "concerning"

    empty: dict = {}
    assert TEMPLATES["bulk_rnaseq"].compute_quality(empty) == "pending_review"

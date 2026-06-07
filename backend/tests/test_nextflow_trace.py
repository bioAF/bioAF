"""Tests for the shared Nextflow trace parser (BAL rework, Phase 5).

Nextflow trace.tsv parsing is domain logic (it describes a Nextflow run), not
Kubernetes logic. Before this phase it lived twice: a raw row parse in
``services/pipeline_monitor_service.py`` (which normalized separately) and a
parse+normalize+aggregate in ``adapters/compute/kubernetes.py``. The two
normalizers had drifted (the monitor handled ``KB``/bare-float memory; the
adapter handled only ``GB``/``MB``).

This module is the single source of truth, importable by both an adapter and a
service without violating the BAL layering rule. Per the owner decision, the
normalizers converge to the *superset* (the monitor's richer behavior), so the
adapter's progress view gains KB/bare-float memory parsing.
"""

import pytest

from app.pipeline import nextflow_trace as nf


# A representative trace.tsv with the full Nextflow column set.
SAMPLE_TRACE_TSV = (
    "task_id\thash\tnative_id\tprocess\ttag\tname\tstatus\texit\tsubmit\tstart\t"
    "complete\tduration\trealtime\t%cpu\tpeak_rss\tpeak_vmem\trchar\twchar\n"
    "1\tab/123\t100\tSTARSOLO\t-\tSTARSOLO (S1)\tCOMPLETED\t0\tt\tt\tt\t30m\t29m 45s\t85.2\t4.5 GB\t8.1 GB\t100\t200\n"
    "2\tcd/789\t101\tSAMTOOLS_SORT\t-\tSAMTOOLS_SORT (S1)\tRUNNING\t-\tt\tt\t-\t-\t-\t-\t-\t-\t-\t-\n"
    "3\tef/345\t102\tFASTQC\t-\tFASTQC (S1)\tCACHED\t0\tt\tt\tt\t5m\t4m 30s\t20.5\t500 MB\t1.2 GB\t50\t10\n"
)


# --- raw row parse (what the monitor consumes) ------------------------------


def test_parse_trace_rows_returns_raw_dicts():
    rows = nf.parse_trace_rows(SAMPLE_TRACE_TSV)
    assert len(rows) == 3
    # raw, unnormalized values keyed by the TSV header
    assert rows[0]["process"] == "STARSOLO"
    assert rows[0]["status"] == "COMPLETED"
    assert rows[0]["exit"] == "0"
    assert rows[1]["status"] == "RUNNING"
    assert rows[2]["status"] == "CACHED"


def test_parse_trace_rows_empty():
    assert nf.parse_trace_rows("") == []
    assert nf.parse_trace_rows("task_id\tstatus\n") == []


# --- normalizers (the converged superset) -----------------------------------


def test_map_nf_status():
    assert nf.map_nf_status("COMPLETED") == "completed"
    assert nf.map_nf_status("RUNNING") == "running"
    assert nf.map_nf_status("FAILED") == "failed"
    assert nf.map_nf_status("CACHED") == "cached"
    assert nf.map_nf_status("SUBMITTED") == "pending"
    assert nf.map_nf_status("PENDING") == "pending"
    assert nf.map_nf_status("ABORTED") == "failed"
    # unknown statuses lower-case through
    assert nf.map_nf_status("WEIRD") == "weird"


def test_parse_memory_gb_superset():
    assert nf.parse_memory_gb("4.5 GB") == 4.5
    assert nf.parse_memory_gb("500 MB") == pytest.approx(0.49, rel=0.1)
    # superset behaviors the adapter previously lacked:
    assert nf.parse_memory_gb("1048576 KB") == pytest.approx(1.0, rel=0.01)
    assert nf.parse_memory_gb("2.0") == 2.0  # bare float = GB
    assert nf.parse_memory_gb("-") is None
    assert nf.parse_memory_gb(None) is None
    assert nf.parse_memory_gb("") is None


def test_parse_duration_s():
    assert nf.parse_duration_s("30s") == 30
    assert nf.parse_duration_s("5m 30s") == 330
    assert nf.parse_duration_s("1h 2m 3s") == 3723
    assert nf.parse_duration_s("250ms") == 0
    assert nf.parse_duration_s("-") is None
    assert nf.parse_duration_s(None) is None


def test_safe_int_and_float():
    assert nf.safe_int("5") == 5
    assert nf.safe_int("-") is None
    assert nf.safe_int("") is None
    assert nf.safe_float("85.2") == 85.2
    assert nf.safe_float("85.2%") == 85.2
    assert nf.safe_float("-") is None


# --- the aggregate progress structure (what the adapter returns) ------------


def test_parse_trace_to_progress_shape_and_values():
    result = nf.parse_trace_to_progress(SAMPLE_TRACE_TSV)
    # 2 of 3 are completed/cached -> 66.7%
    assert result["percent_complete"] == 66.7
    procs = result["processes"]
    assert len(procs) == 3
    first = procs[0]
    assert first["task_id"] == "1"
    assert first["name"] == "STARSOLO (S1)"
    assert first["status"] == "completed"
    assert first["cpu"] == 85.2
    assert first["memory_gb"] == 4.5
    assert first["duration_s"] == 1785
    assert first["attempt"] == 1
    # running row coerces unparseable numeric fields to zero defaults
    assert procs[1]["status"] == "running"
    assert procs[1]["cpu"] == 0.0
    assert procs[1]["memory_gb"] == 0.0
    assert procs[1]["duration_s"] == 0
    assert procs[2]["status"] == "cached"


def test_parse_trace_to_progress_empty():
    assert nf.parse_trace_to_progress("") == {"percent_complete": 0.0, "processes": []}


def test_parse_trace_to_progress_gains_kb_memory():
    """The converged (superset) parser now resolves KB memory the adapter dropped."""
    trace = (
        "task_id\tname\tstatus\t%cpu\tpeak_rss\trealtime\n"
        "1\tP\tCOMPLETED\t10\t2097152 KB\t10s\n"
    )
    result = nf.parse_trace_to_progress(trace)
    assert result["processes"][0]["memory_gb"] == pytest.approx(2.0, rel=0.01)

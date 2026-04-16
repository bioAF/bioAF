"""Unit tests for the Google Sheets service.

All Sheets API calls are mocked -- no real Google API calls are made.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.google_sheets_service import (
    _resolve_sheet_name,
    get_sheet_names,
    parse_sheet_url,
    read_header_row,
)


# ---------------------------------------------------------------------------
# parse_sheet_url tests
# ---------------------------------------------------------------------------


def test_parse_basic_edit_url():
    url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"
    sid, gid = parse_sheet_url(url)
    assert sid == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
    assert gid is None


def test_parse_url_with_gid():
    url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit#gid=123"
    sid, gid = parse_sheet_url(url)
    assert sid == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
    assert gid == 123


def test_parse_url_with_gid_in_query():
    url = "https://docs.google.com/spreadsheets/d/abc123/edit?usp=sharing&gid=456"
    sid, gid = parse_sheet_url(url)
    assert sid == "abc123"
    assert gid == 456


def test_parse_url_view_mode():
    url = "https://docs.google.com/spreadsheets/d/abc-def_123/view"
    sid, gid = parse_sheet_url(url)
    assert sid == "abc-def_123"
    assert gid is None


def test_parse_url_no_trailing_path():
    url = "https://docs.google.com/spreadsheets/d/abc123"
    sid, gid = parse_sheet_url(url)
    assert sid == "abc123"
    assert gid is None


def test_parse_invalid_url_raises():
    with pytest.raises(ValueError, match="Not a valid Google Sheets URL"):
        parse_sheet_url("https://example.com/not-a-sheet")


def test_parse_non_sheets_google_url_raises():
    with pytest.raises(ValueError, match="Not a valid Google Sheets URL"):
        parse_sheet_url("https://docs.google.com/document/d/abc123/edit")


# ---------------------------------------------------------------------------
# _resolve_sheet_name tests
# ---------------------------------------------------------------------------


def _mock_spreadsheet_meta(sheets_data: list[dict]) -> MagicMock:
    """Build a mock Sheets service with spreadsheet metadata."""
    service = MagicMock()
    service.spreadsheets().get().execute.return_value = {"sheets": sheets_data}
    return service


def test_resolve_first_sheet_when_no_gid():
    service = _mock_spreadsheet_meta(
        [
            {"properties": {"sheetId": 0, "title": "Sheet1"}},
            {"properties": {"sheetId": 123, "title": "Data"}},
        ]
    )
    assert _resolve_sheet_name(service, "abc", None) == "Sheet1"


def test_resolve_sheet_by_gid():
    service = _mock_spreadsheet_meta(
        [
            {"properties": {"sheetId": 0, "title": "Sheet1"}},
            {"properties": {"sheetId": 456, "title": "Samples"}},
        ]
    )
    assert _resolve_sheet_name(service, "abc", 456) == "Samples"


def test_resolve_sheet_unknown_gid_raises():
    service = _mock_spreadsheet_meta(
        [
            {"properties": {"sheetId": 0, "title": "Sheet1"}},
        ]
    )
    with pytest.raises(ValueError, match="No sheet found with gid=999"):
        _resolve_sheet_name(service, "abc", 999)


def test_resolve_sheet_empty_spreadsheet_raises():
    service = _mock_spreadsheet_meta([])
    with pytest.raises(ValueError, match="Spreadsheet has no sheets"):
        _resolve_sheet_name(service, "abc", None)


# ---------------------------------------------------------------------------
# read_header_row tests
# ---------------------------------------------------------------------------


def _build_sheets_mock(sheet_title: str, header_values: list) -> MagicMock:
    """Build a mock discovery service returning given headers."""
    mock_service = MagicMock()

    # Mock spreadsheets().get() for _resolve_sheet_name
    mock_service.spreadsheets().get().execute.return_value = {
        "sheets": [{"properties": {"sheetId": 0, "title": sheet_title}}]
    }

    # Mock spreadsheets().values().get() for header row
    mock_service.spreadsheets().values().get().execute.return_value = {
        "values": [header_values] if header_values else []
    }

    return mock_service


@patch("app.services.google_sheets_service.discovery_build")
def test_read_header_row_returns_headers(mock_build):
    mock_build.return_value = _build_sheets_mock("Sheet1", ["Name", "Type", "Date"])
    headers, sheet_name = read_header_row("fake_creds", "spreadsheet_id")
    assert headers == ["Name", "Type", "Date"]
    assert sheet_name == "Sheet1"


@patch("app.services.google_sheets_service.discovery_build")
def test_read_header_row_strips_whitespace(mock_build):
    mock_build.return_value = _build_sheets_mock("Data", ["  Name  ", "Type", " Date"])
    headers, _ = read_header_row("fake_creds", "sid")
    assert headers == ["Name", "Type", "Date"]


@patch("app.services.google_sheets_service.discovery_build")
def test_read_header_row_skips_empty_cells(mock_build):
    mock_build.return_value = _build_sheets_mock("Sheet1", ["Name", "", "Type", "  ", "Date"])
    headers, _ = read_header_row("fake_creds", "sid")
    assert headers == ["Name", "Type", "Date"]


@patch("app.services.google_sheets_service.discovery_build")
def test_read_header_row_empty_raises(mock_build):
    mock_service = MagicMock()
    mock_service.spreadsheets().get().execute.return_value = {
        "sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}]
    }
    mock_service.spreadsheets().values().get().execute.return_value = {"values": []}
    mock_build.return_value = mock_service

    with pytest.raises(ValueError, match="first row is empty"):
        read_header_row("fake_creds", "sid")


# ---------------------------------------------------------------------------
# get_sheet_names tests
# ---------------------------------------------------------------------------


@patch("app.services.google_sheets_service.discovery_build")
def test_get_sheet_names(mock_build):
    mock_service = MagicMock()
    mock_service.spreadsheets().get().execute.return_value = {
        "sheets": [
            {"properties": {"title": "Sheet1"}},
            {"properties": {"title": "Metadata"}},
        ]
    }
    mock_build.return_value = mock_service
    names = get_sheet_names("creds", "sid")
    assert names == ["Sheet1", "Metadata"]

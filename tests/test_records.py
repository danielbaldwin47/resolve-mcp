"""Record files: what ``write`` leaves, and what ``rows`` will and will not read back.

The reader is the strong one — it refuses a file it cannot vouch for rather than handing
its caller a half-file — so the refusals are tested here, once, at the interface every
caller now shares (#222). A caller-side test would only be re-testing this one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resolve_mcp.analysis import records
from resolve_mcp.errors import InvalidRequestError


def _written(path: Path, rows: list[dict[str, object]], field: str = "beats") -> Path:
    return records.write(path, {"kind": field, "count": len(rows)}, field, rows)


# --- what reads back -------------------------------------------------------------------


def test_the_records_come_back_as_they_were_written(tmp_path: Path) -> None:
    path = _written(tmp_path / "beats.json", [{"t": 0.0, "beat": 1}, {"t": 0.5, "beat": 2}])

    assert records.rows(path, "beats") == ({"t": 0.0, "beat": 1}, {"t": 0.5, "beat": 2})


def test_the_header_keys_are_not_records(tmp_path: Path) -> None:
    """Only the named field's list is read — a header key beside it is not a row."""
    path = _written(tmp_path / "beats.json", [{"t": 1.0}])

    assert records.rows(path, "beats") == ({"t": 1.0},)
    assert records.read(path)["kind"] == "beats"


def test_records_come_back_in_time_order_whatever_order_they_sit_in(tmp_path: Path) -> None:
    """A file the writer did not sort must not read as a curve that jumps backwards."""
    path = _written(tmp_path / "energy.json", [{"t": 2.0}, {"t": 0.5}, {"t": 1.0}], "energy")

    assert [row["t"] for row in records.rows(path, "energy")] == [0.5, 1.0, 2.0]


def test_a_record_with_no_time_in_it_is_dropped_not_read_as_time_zero(tmp_path: Path) -> None:
    path = _written(tmp_path / "beats.json", [{"t": 1.0}, {"beat": 2}, {"t": "late"}, {"t": 0.0}])

    assert [row["t"] for row in records.rows(path, "beats")] == [0.0, 1.0]


# --- what it refuses -------------------------------------------------------------------


def test_a_file_that_is_not_there_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(InvalidRequestError) as caught:
        records.rows(tmp_path / "absent.json", "beats")

    detail = caught.value.payload()["detail"]
    assert detail == {"file": str(tmp_path / "absent.json"), "field": "beats"}


def test_a_file_that_is_not_json_is_refused_rather_than_raised_through(tmp_path: Path) -> None:
    path = tmp_path / "beats.json"
    path.write_text("not json at all", encoding="utf-8")

    with pytest.raises(InvalidRequestError) as caught:
        records.rows(path, "beats")

    assert "as analysis JSON" in caught.value.payload()["cause"]


def test_a_file_holding_no_such_field_is_refused_by_field_name(tmp_path: Path) -> None:
    path = _written(tmp_path / "tunes.json", [{"t": 0.0}], "tunes")

    with pytest.raises(InvalidRequestError) as caught:
        records.rows(path, "beats")

    payload = caught.value.payload()
    assert "holds no 'beats' records" in payload["cause"]
    assert payload["detail"]["field"] == "beats"


def test_a_field_that_is_not_a_list_is_refused_like_a_missing_one(tmp_path: Path) -> None:
    path = tmp_path / "beats.json"
    path.write_text(json.dumps({"beats": {"t": 0.0}}), encoding="utf-8")

    with pytest.raises(InvalidRequestError):
        records.rows(path, "beats")


def test_json_that_is_not_a_document_at_all_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "beats.json"
    path.write_text(json.dumps([{"t": 0.0}]), encoding="utf-8")

    with pytest.raises(InvalidRequestError):
        records.rows(path, "beats")


def test_a_file_that_measured_nothing_is_refused_not_read_as_a_file_nobody_named(
    tmp_path: Path,
) -> None:
    """Empty and absent would both leave the column null, and they are not the same call."""
    path = tmp_path / "beats.json"
    path.write_text(json.dumps({"beats": []}), encoding="utf-8")

    with pytest.raises(InvalidRequestError) as caught:
        records.rows(path, "beats")

    assert "record with a time in it" in caught.value.payload()["cause"]


def test_a_file_whose_records_all_lack_a_time_is_the_same_refusal(tmp_path: Path) -> None:
    path = _written(tmp_path / "beats.json", [{"beat": 1}, {"beat": 2}])

    with pytest.raises(InvalidRequestError) as caught:
        records.rows(path, "beats")

    assert "record with a time in it" in caught.value.payload()["cause"]

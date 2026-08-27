"""Offline tests for the pure parsing helpers.

No network: these must stay fast and deterministic so they can gate every push.
The live contract with NOAA is covered separately in test_live.py.
"""
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


# --- _parse_kp -------------------------------------------------------------

def test_parses_capital_kp_from_observed_feed():
    """Regression: NOAA's observed feed spells the key "Kp".

    This shipped broken — the parser only matched lowercase keys, so every
    row failed float(None), was skipped, and the tool reported
    "No Kp data available" against a healthy 200 response.
    """
    rows = [{"time_tag": "2026-08-27T15:00:00", "Kp": 0.67, "station_count": 8}]
    points = server._parse_kp(rows)
    assert len(points) == 1
    assert points[0]["kp"] == 0.67


def test_parses_lowercase_kp_from_forecast_feed():
    rows = [{"time_tag": "2026-08-27T15:00:00", "kp": 2.67, "observed": "observed"}]
    points = server._parse_kp(rows)
    assert len(points) == 1
    assert points[0]["kp"] == 2.67
    assert points[0]["observed"] == "observed"


def test_key_precedence_is_preserved():
    """kp_index outranks estimated_kp, which outranks kp — regardless of casing."""
    rows = [{"time_tag": "2026-08-27T15:00:00",
             "Kp": 1.0, "estimated_kp": 2.0, "kp_index": 3.0}]
    assert server._parse_kp(rows)[0]["kp"] == 3.0

    rows = [{"time_tag": "2026-08-27T15:00:00", "kp": 1.0, "estimated_kp": 2.0}]
    assert server._parse_kp(rows)[0]["kp"] == 2.0


def test_list_rows_drop_the_header():
    rows = [["time_tag", "Kp", "observed"],
            ["2026-08-27 12:00:00", "3.0", "observed"],
            ["2026-08-27 15:00:00", "4.0", "estimated"]]
    points = server._parse_kp(rows)
    assert [p["kp"] for p in points] == [3.0, 4.0]
    assert [p["observed"] for p in points] == ["observed", "estimated"]


def test_malformed_rows_are_skipped_not_fatal():
    rows = [
        {"time_tag": "2026-08-27T15:00:00", "Kp": 1.0},   # good
        {"time_tag": "2026-08-27T18:00:00", "Kp": None},  # unparseable value
        {"time_tag": "not a date", "Kp": 2.0},            # unparseable time
        {"Kp": 3.0},                                      # no time at all
        {"time_tag": "2026-08-27T21:00:00"},              # no Kp at all
    ]
    points = server._parse_kp(rows)
    assert [p["kp"] for p in points] == [1.0]


def test_non_list_input_is_empty_not_an_exception():
    assert server._parse_kp({"unexpected": "shape"}) == []
    assert server._parse_kp(None) == []


def test_points_come_back_in_chronological_order():
    rows = [{"time_tag": "2026-08-27T18:00:00", "Kp": 2.0},
            {"time_tag": "2026-08-27T12:00:00", "Kp": 1.0}]
    assert [p["kp"] for p in server._parse_kp(rows)] == [1.0, 2.0]


# --- _level ----------------------------------------------------------------

@pytest.mark.parametrize("kp,level", [
    (0.0, "low"), (2.99, "low"),
    (3.0, "moderate"), (4.99, "moderate"),
    (5.0, "high"), (6.99, "high"),
    (7.0, "severe"), (9.0, "severe"),
])
def test_level_boundaries(kp, level):
    assert server._level(kp) == level


# --- _flux_to_class --------------------------------------------------------

@pytest.mark.parametrize("flux,cls", [
    (1e-4, "X1.0"), (5.3e-4, "X5.3"),
    (1e-5, "M1.0"), (6.9e-5, "M6.9"),
    (1e-6, "C1.0"), (5.3e-6, "C5.3"),
    (1e-7, "B1.0"), (4.7e-7, "B4.7"),
    (5e-8, "A5.0"),
])
def test_flux_to_class(flux, cls):
    assert server._flux_to_class(flux) == cls


@pytest.mark.parametrize("flux", [None, 0, -1e-6])
def test_flux_to_class_rejects_nonsense(flux):
    assert server._flux_to_class(flux) == "?"


# --- _kp_needed ------------------------------------------------------------

@pytest.mark.parametrize("lat,kp", [
    (70, 1), (66, 1), (63, 3), (62, 3), (59, 4.5), (58, 4.5),
    (56, 6), (55, 6), (53, 7), (52, 7), (48.2, 8), (48, 8), (45, 9), (44.5, 9),
])
def test_aurora_thresholds_match_the_documented_table(lat, kp):
    assert server._kp_needed(lat) == kp


def test_below_the_table_returns_none():
    assert server._kp_needed(40) is None


def test_southern_hemisphere_mirrors_northern():
    assert server._kp_needed(-48.2) == server._kp_needed(48.2)


# --- _forecast_days / _next24_max ------------------------------------------

def _pt(hours_from_now, kp, observed=""):
    return {"time": datetime.now(timezone.utc) + timedelta(hours=hours_from_now),
            "kp": kp, "observed": observed}


def test_forecast_days_takes_the_daily_maximum():
    points = [_pt(1, 2.0), _pt(2, 5.67), _pt(3, 3.0)]
    days = server._forecast_days(points)
    assert days, "expected at least one day bucket"
    assert max(d["max_kp"] for d in days) == 5.67
    assert all(d["level"] == server._level(d["max_kp"]) for d in days)


def test_forecast_days_caps_at_three():
    points = [_pt(h, 3.0) for h in range(1, 71, 6)]
    assert len(server._forecast_days(points)) <= 3


def test_observed_points_are_excluded_from_forecasts():
    points = [_pt(1, 9.0, "observed"), _pt(2, 2.0, "predicted")]
    assert server._next24_max(points) == 2.0


def test_next24_max_ignores_points_beyond_the_window():
    points = [_pt(1, 2.0), _pt(48, 9.0)]
    assert server._next24_max(points) == 2.0


def test_next24_max_is_none_without_usable_points():
    assert server._next24_max([]) is None


# --- _normalize_time -------------------------------------------------------

@pytest.mark.parametrize("raw,out", [
    ("2026-08-27T15:00:00", "2026-08-27T15:00:00Z"),
    ("2026-08-27T15:00:00Z", "2026-08-27T15:00:00Z"),
    ("2026-08-27 15:00:00", "2026-08-27T15:00:00Z"),
    ("2026-08-27T15:00:00+00:00", "2026-08-27T15:00:00+00:00"),
    ("", ""),
])
def test_normalize_time(raw, out):
    assert server._normalize_time(raw) == out


def test_parse_time_rejects_garbage():
    assert server._parse_time("definitely not a timestamp") is None


# --- version consistency ---------------------------------------------------

def test_declared_version_matches_server_json():
    """server.py and server.json drifted once; keep them pinned together."""
    import json
    manifest = json.loads((Path(__file__).resolve().parent.parent / "server.json").read_text())
    assert server.mcp.version == manifest["version"]

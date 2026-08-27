"""Live contract tests against NOAA SWPC.

These hit the network on purpose. The bug that shipped to production was not a
logic error — the code was fine until NOAA's observed feed turned out to spell
its key "Kp" while the forecast feed says "kp". Only a real call catches that
class of drift, so these run on a schedule rather than gating every push.

    pytest -m live
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402

pytestmark = pytest.mark.live

TOOLS = ["get_current_kp", "get_kp_forecast_3day",
         "get_solar_flares_24h", "get_aurora_visibility"]


def call(name, *args):
    fn = getattr(server, name)
    fn = getattr(fn, "fn", None) or getattr(fn, "func", None) or fn
    return fn(*args)


@pytest.mark.parametrize("name", TOOLS)
def test_tool_returns_data_not_an_error(name):
    result = call(name, 48.2) if name == "get_aurora_visibility" else call(name)
    assert isinstance(result, dict)
    assert "error" not in result, f"{name} returned {result['error']!r}"


def test_current_kp_is_a_plausible_reading():
    r = call("get_current_kp")
    assert 0 <= r["kp"] <= 9
    assert r["level"] == server._level(r["kp"])
    assert r["max_24h"] >= r["kp"] or r["max_24h"] is None
    assert r["time_utc"]


def test_forecast_covers_up_to_three_days():
    r = call("get_kp_forecast_3day")
    assert 1 <= len(r["days"]) <= 3
    dates = [d["date"] for d in r["days"]]
    assert dates == sorted(dates), "days must be chronological"
    for d in r["days"]:
        assert 0 <= d["max_kp"] <= 9
        assert d["level"] == server._level(d["max_kp"])


def test_flare_classes_are_well_formed():
    r = call("get_solar_flares_24h")
    for key in ("current_class", "peak_24h_class"):
        assert r[key][0] in "ABCMX", f"{key} = {r[key]!r}"
        float(r[key][1:])  # raises if the numeric part is malformed


def test_aurora_verdict_is_consistent_with_the_numbers():
    r = call("get_aurora_visibility", 48.2)
    assert r["kp_needed_naked_eye"] == 8
    assert r["visible_tonight"] == (r["forecast_max_kp_24h"] >= 8)


def test_latitude_is_validated():
    assert "error" in call("get_aurora_visibility", 999)
    assert "error" in call("get_aurora_visibility", -91)

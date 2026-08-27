"""
solarstorm.today MCP server
---------------------------
Exposes live NOAA space-weather data as MCP tools, mirroring the logic
that powers https://solarstorm.today (same endpoints, same day bucketing,
same visibility thresholds).

Run locally (stdio, for Claude Desktop / Claude Code):
    python server.py

Run as a remote server (streamable HTTP, e.g. on a VPS behind nginx):
    MCP_TRANSPORT=streamable-http MCP_PORT=8765 python server.py
"""
import json
import math
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer

NOAA = "https://services.swpc.noaa.gov"
BERLIN = ZoneInfo("Europe/Berlin")
UA = "solarstorm.today MCP server (contact: hello@solarstorm.today)"

mcp = MCPServer(
    name="solarstorm",
    title="solarstorm.today — live space weather",
    description="Live Kp index, 3-day geomagnetic forecast, solar flare activity "
                "and aurora visibility, powered by NOAA SWPC data.",
    website_url="https://solarstorm.today",
    version="1.0.0",
)


# ---------- helpers (mirrors of the site's JS logic) ----------

def _fetch(path: str, timeout: int = 10):
    req = urllib.request.Request(NOAA + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _normalize_time(value: str) -> str:
    if not isinstance(value, str) or not value:
        return ""
    if "T" in value:
        return value if value.endswith("Z") or "+" in value[-6:] else value + "Z"
    return value.replace(" ", "T") + "Z"


def _parse_time(value: str) -> datetime | None:
    iso = _normalize_time(value)
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_kp(raw) -> list[dict]:
    """NOAA 'products' tables: first row is the header."""
    if not isinstance(raw, list):
        return []
    points = []
    rows = raw[1:] if raw and isinstance(raw[0], list) else raw
    for row in rows:
        if isinstance(row, list):
            t, kp, observed = row[0], row[1], (row[2] or "")
        else:
            t = row.get("time_tag") or row.get("datetime")
            kp = row.get("kp_index", row.get("estimated_kp", row.get("kp")))
            observed = row.get("observed") or row.get("status") or ""
        ts = _parse_time(t)
        try:
            kp = float(kp)
        except (TypeError, ValueError):
            continue
        if ts is not None and math.isfinite(kp):
            points.append({"time": ts, "kp": kp, "observed": str(observed).strip().lower()})
    points.sort(key=lambda p: p["time"])
    return points


def _level(kp: float) -> str:
    return "severe" if kp >= 7 else "high" if kp >= 5 else "moderate" if kp >= 3 else "low"


def _forecast_days(points: list[dict]) -> list[dict]:
    """Max forecast Kp per Europe/Berlin calendar day, next 3 days."""
    now = datetime.now(timezone.utc)
    days: dict[str, float] = {}
    for p in points:
        if p["observed"] not in ("", "estimated", "predicted"):
            continue
        if p["time"] < now - timedelta(hours=3) or p["time"] > now + timedelta(hours=72):
            continue
        key = p["time"].astimezone(BERLIN).strftime("%Y-%m-%d")
        days[key] = max(days.get(key, 0.0), p["kp"])
    return [
        {"date": d, "max_kp": round(k, 2), "level": _level(k)}
        for d, k in sorted(days.items())[:3]
    ]


def _next24_max(points: list[dict]) -> float | None:
    now = datetime.now(timezone.utc)
    vals = [
        p["kp"] for p in points
        if p["observed"] in ("", "estimated", "predicted")
        and now - timedelta(hours=3) <= p["time"] <= now + timedelta(hours=24)
    ]
    return max(vals) if vals else None


def _flux_to_class(flux: float) -> str:
    """GOES 0.1–0.8 nm X-ray flux (W/m²) → flare class (A/B/C/M/X)."""
    if flux is None or flux <= 0:
        return "?"
    for letter, base in (("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7)):
        if flux >= base:
            return f"{letter}{flux / base:.1f}"
    return f"A{flux / 1e-8:.1f}"


# Aurora visibility: minimal Kp for naked-eye aurora by geographic latitude
# (European-sector guide values, same table as solarstorm.today/aurora-tonight)
_THRESHOLDS = [(66, 1), (62, 3), (58, 4.5), (55, 6), (52, 7), (48, 8), (44.5, 9)]


def _kp_needed(lat: float) -> float | None:
    lat = abs(lat)
    for min_lat, kp in _THRESHOLDS:
        if lat >= min_lat:
            return kp
    return None  # below ~44.5° — only extreme historical events


# ---------- tools ----------

@mcp.tool(description="Current planetary Kp index: latest value, level "
                      "(low/moderate/high/severe) and the observed 24-hour maximum.")
def get_current_kp() -> dict:
    try:
        points = _parse_kp(_fetch("/products/noaa-planetary-k-index.json"))
    except Exception as e:  # network / format errors surface as data, not crashes
        return {"error": f"NOAA request failed: {e}"}
    if not points:
        return {"error": "No Kp data available"}
    now = datetime.now(timezone.utc)
    last = points[-1]
    day = [p["kp"] for p in points if p["time"] >= now - timedelta(hours=24)]
    return {
        "kp": round(last["kp"], 2),
        "level": _level(last["kp"]),
        "time_utc": last["time"].isoformat(),
        "max_24h": round(max(day), 2) if day else None,
        "source": "NOAA SWPC",
    }


@mcp.tool(description="Official NOAA 3-day geomagnetic forecast: the maximum "
                      "expected Kp per calendar day (Europe/Berlin days).")
def get_kp_forecast_3day() -> dict:
    try:
        points = _parse_kp(_fetch("/products/noaa-planetary-k-index-forecast.json"))
    except Exception as e:
        return {"error": f"NOAA request failed: {e}"}
    days = _forecast_days(points)
    if not days:
        return {"error": "No forecast data available"}
    return {"days": days, "timezone": "Europe/Berlin", "source": "NOAA SWPC",
            "note": "Kp>=5 is a G1 geomagnetic storm on the NOAA scale."}


@mcp.tool(description="Solar X-ray activity of the past 24 hours from GOES: "
                      "current flare class and the 24-hour peak class.")
def get_solar_flares_24h() -> dict:
    try:
        raw = _fetch("/json/goes/primary/xrays-1-day.json")
    except Exception as e:
        return {"error": f"NOAA request failed: {e}"}
    series = [r for r in raw if isinstance(r, dict)
              and r.get("energy") == "0.1-0.8nm" and r.get("flux") is not None]
    if not series:
        return {"error": "No GOES X-ray data available"}
    current = series[-1]
    peak = max(series, key=lambda r: r["flux"])
    return {
        "current_class": _flux_to_class(float(current["flux"])),
        "peak_24h_class": _flux_to_class(float(peak["flux"])),
        "peak_time_utc": _normalize_time(str(peak.get("time_tag", ""))),
        "source": "NOAA GOES (0.1-0.8 nm)",
    }


@mcp.tool(description="Aurora visibility outlook for a geographic latitude "
                      "(degrees, e.g. 48.2 for Vienna): the Kp you need there, "
                      "the expected maximum Kp in the next 24 h, and a verdict.")
def get_aurora_visibility(latitude: float) -> dict:
    if not -90 <= latitude <= 90:
        return {"error": "latitude must be between -90 and 90"}
    try:
        points = _parse_kp(_fetch("/products/noaa-planetary-k-index-forecast.json"))
    except Exception as e:
        return {"error": f"NOAA request failed: {e}"}
    max24 = _next24_max(points)
    if max24 is None:
        return {"error": "No forecast data available"}
    needed = _kp_needed(latitude)
    if needed is None:
        verdict = "Practically never visible at this latitude (only extreme historical storms)."
        visible = False
    else:
        visible = max24 >= needed
        margin = round(max24 - needed, 1)
        if visible:
            verdict = f"Possible: forecast max Kp {max24:.1f} meets the ~Kp {needed} needed at {abs(latitude):.1f}°."
        else:
            verdict = (f"Unlikely: forecast max Kp {max24:.1f} is {abs(margin):.1f} below "
                       f"the ~Kp {needed} needed at {abs(latitude):.1f}°. "
                       f"A camera picks aurora up roughly one Kp level earlier.")
    return {
        "latitude": latitude,
        "kp_needed_naked_eye": needed,
        "forecast_max_kp_24h": round(max24, 2),
        "visible_tonight": visible,
        "verdict": verdict,
        "details": "https://solarstorm.today/aurora-tonight/",
    }


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.run(transport="streamable-http",
                host=os.environ.get("MCP_HOST", "0.0.0.0"),
                port=int(os.environ.get("MCP_PORT", "8765")))
    else:
        mcp.run()

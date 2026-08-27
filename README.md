# solarstorm.today MCP server

[![MCP Registry](https://img.shields.io/badge/MCP%20Registry-today.solarstorm%2Fsolarstorm-2b7fff)](https://registry.modelcontextprotocol.io/v0.1/servers/today.solarstorm%2Fsolarstorm/versions/latest)
[![Registry version](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fregistry.modelcontextprotocol.io%2Fv0.1%2Fservers%2Ftoday.solarstorm%252Fsolarstorm%2Fversions%2Flatest&query=%24.server.version&label=registry&prefix=v&color=2b7fff)](https://registry.modelcontextprotocol.io/v0.1/servers/today.solarstorm%2Fsolarstorm/versions/latest)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Hosted](https://img.shields.io/badge/hosted-mcp.solarstorm.today-f5a524)](https://solarstorm.today/mcp/)

Live space weather for AI assistants. This MCP server exposes NOAA SWPC data —
the planetary Kp index, the official 3-day geomagnetic forecast, GOES solar
flare activity, and aurora visibility for any latitude — as four tools your
agent can call directly.

It runs the same logic as [solarstorm.today](https://solarstorm.today), so
answers match what the site shows. No API keys, no accounts, no state.

> **Ask your assistant:** *"Will I see the aurora in Vienna tonight?"* →
> it calls `get_aurora_visibility(48.2)` and answers from live NOAA data.

## Tools

| Tool | Returns |
|---|---|
| `get_current_kp()` | Latest planetary Kp, level (`low`/`moderate`/`high`/`severe`), observed 24-hour maximum |
| `get_kp_forecast_3day()` | Maximum forecast Kp per calendar day for the next 3 days (Europe/Berlin days) |
| `get_solar_flares_24h()` | Current GOES X-ray flare class and the 24-hour peak, with peak time |
| `get_aurora_visibility(latitude)` | Kp needed at that latitude, forecast max Kp for the next 24 h, and a plain-language verdict |

<details>
<summary>Example responses</summary>

```jsonc
// get_current_kp()
{
  "kp": 4.33,
  "level": "moderate",
  "time_utc": "2026-08-27T18:00:00+00:00",
  "max_24h": 5.67,
  "source": "NOAA SWPC"
}

// get_kp_forecast_3day()
{
  "days": [
    { "date": "2026-08-27", "max_kp": 5.67, "level": "high" },
    { "date": "2026-08-28", "max_kp": 4.0,  "level": "moderate" },
    { "date": "2026-08-29", "max_kp": 2.33, "level": "low" }
  ],
  "timezone": "Europe/Berlin",
  "source": "NOAA SWPC",
  "note": "Kp>=5 is a G1 geomagnetic storm on the NOAA scale."
}

// get_solar_flares_24h()
{
  "current_class": "C2.1",
  "peak_24h_class": "M6.9",
  "peak_time_utc": "2026-08-27T04:12:00Z",
  "source": "NOAA GOES (0.1-0.8 nm)"
}

// get_aurora_visibility(48.2)
{
  "latitude": 48.2,
  "kp_needed_naked_eye": 8,
  "forecast_max_kp_24h": 5.67,
  "visible_tonight": false,
  "verdict": "Unlikely: forecast max Kp 5.7 is 2.3 below the ~Kp 8 needed at 48.2°. A camera picks aurora up roughly one Kp level earlier.",
  "details": "https://solarstorm.today/aurora-tonight/"
}
```

</details>

Network or format errors from NOAA come back as an `error` field. The server
never crashes on a bad upstream response.

## Use the hosted server (no install)

A public instance is running at `https://mcp.solarstorm.today/mcp`.

**Claude Code**

```bash
claude mcp add --transport http solarstorm https://mcp.solarstorm.today/mcp
```

**Claude Desktop / claude.ai** — add a custom connector pointing at
`https://mcp.solarstorm.today/mcp`.

**Any other MCP client** — streamable HTTP transport, same URL, no auth.

## Run it locally (stdio)

Requires Python 3.10+ — the floor set by the `mcp` SDK.

```bash
git clone https://github.com/newminya/solarstorm-mcp.git
cd solarstorm-mcp
pip install -r requirements.txt
python server.py          # waits for a stdio client; Ctrl+C to quit
```

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "solarstorm": {
      "command": "python",
      "args": ["/absolute/path/to/solarstorm-mcp/server.py"]
    }
  }
}
```

Claude Code:

```bash
claude mcp add solarstorm -- python /absolute/path/to/solarstorm-mcp/server.py
```

## Self-host it (streamable HTTP)

Three environment variables control the transport:

| Variable | Default | Purpose |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | Set to `streamable-http` for a network server |
| `MCP_HOST` | `0.0.0.0` | Bind address — use `127.0.0.1` behind a reverse proxy |
| `MCP_PORT` | `8765` | Listen port |

<details>
<summary>systemd + reverse proxy</summary>

```bash
mkdir -p /opt/solarstorm-mcp && cd /opt/solarstorm-mcp
# copy server.py and requirements.txt here
python3 -m venv venv && venv/bin/pip install -r requirements.txt
```

`/etc/systemd/system/solarstorm-mcp.service`:

```ini
[Unit]
Description=solarstorm.today MCP server
After=network.target

[Service]
WorkingDirectory=/opt/solarstorm-mcp
Environment=MCP_TRANSPORT=streamable-http
Environment=MCP_HOST=127.0.0.1
Environment=MCP_PORT=8765
ExecStart=/opt/solarstorm-mcp/venv/bin/python server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now solarstorm-mcp
systemctl status solarstorm-mcp
```

nginx in front, on a subdomain with a TLS certificate:

```nginx
server {
    server_name mcp.example.com;
    location /mcp {
        proxy_pass http://127.0.0.1:8765/mcp;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
    # + certbot TLS lines
}
```

One thing that bites people: the MCP SDK rejects requests whose `Host` header
doesn't match what it expects (DNS-rebinding protection). If handshakes fail
with a 4xx while the service itself is healthy, check what `Host` your proxy
forwards.

</details>

## How the numbers are derived

- **Data sources.** `services.swpc.noaa.gov` — planetary K-index and its
  forecast product, plus GOES primary 0.1–0.8 nm X-ray flux. 10-second timeout,
  no caching, no keys.
- **Daily maxima** are bucketed by Europe/Berlin calendar days, matching the
  site's German-language audience. Only observed-blank, `estimated`, and
  `predicted` points count toward forecasts.
- **Flare classes** convert X-ray flux to the standard A/B/C/M/X scale
  (X ≥ 1e-4 W/m², M ≥ 1e-5, and so on).
- **Aurora thresholds** are European-sector guide values for naked-eye
  visibility: Kp 1 above 66°, 3 above 62°, 4.5 above 58°, 6 above 55°,
  7 above 52°, 8 above 48°, 9 above 44.5°. Below ~44.5° only extreme
  historical storms qualify. A camera typically catches aurora about one Kp
  level earlier than the eye does.
- **Not modelled:** the Bz component of the interplanetary magnetic field.
  A southward Bz makes a given Kp far more productive, and it can't be
  forecast days ahead — treat verdicts as odds, not promises.

## Related

- [solarstorm.today](https://solarstorm.today) — the site this mirrors (EN/DE)
- [Aurora tonight](https://solarstorm.today/aurora-tonight/) — live map with the NOAA OVATION oval
- [3-day forecast](https://solarstorm.today/3-day-forecast/)
- [About this MCP server](https://solarstorm.today/mcp/)

## License

MIT — see [LICENSE](LICENSE).

# Pico 2W Standalone Power + Wi-Fi Bring-Up (3 Devices)

Goal: each Pico runs from its own power supply, auto-boots, joins Wi-Fi, and reports liveness independently.

## 1) Hardware and power setup

Per Pico 2W:
- 1x USB data cable (for initial flashing)
- 1x dedicated 5V USB power supply (recommended: >= 1A, stable)

Power guidance:
- During Wi-Fi TX bursts, current spikes can destabilize weak supplies.
- Use one supply per Pico while testing distributed behavior.
- If random reboots happen, first suspect power quality/cable quality.

## 2) Flash MicroPython on each board

- Put Pico in BOOTSEL mode.
- Copy MicroPython UF2 for Pico 2W.
- Reconnect normally.

## 3) Upload runtime files

On each Pico upload:
- `main.py` (from this folder)
- `node_config.py` (derived from one of `node_config_pico*.py`)

Set unique `NODE_ID` per board: `pico1`, `pico2`, `pico3`.

## 4) Start local heartbeat server on laptop/server

```bash
python3 heartbeat_server.py --host 0.0.0.0 --port 9000
```

Set each Pico `SERVER_HOST` to your laptop/server LAN IP.

## 5) Move to independent power

- Disconnect USB data cables.
- Plug each Pico into its own USB power supply.
- Verify heartbeats from all 3 devices in server logs.

## 6) Troubleshooting

- No Wi-Fi join: verify SSID/password and 2.4GHz availability.
- Frequent reconnects: replace cable/supply first.
- No heartbeat but Wi-Fi connected: check `SERVER_HOST`/`SERVER_PORT` and LAN reachability.

## 7) Next integration step

When this is stable, replace heartbeat loop with Raft node runtime so each independently powered Pico participates in consensus.

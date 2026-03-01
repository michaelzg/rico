# Raft KV Demo (3 Pico 2W + Coordinator)

This demo gives you a practical Raft playground:
- 3 Raft nodes (intended for Pico 2W running MicroPython)
- 1 central coordinator server that relays node-to-node messages and exposes a tiny control API
- replicated in-memory key/value store (`put` + `get`)

The coordinator is **not** a consensus participant. It only relays messages and forwards client commands.

## Files

- `coordinator.py`: relay + control server
- `raft_node.py`: CPython node implementation (for local simulation)
- `pico_node_micropython.py`: MicroPython node implementation for Pico 2W
- `control_client.py`: command-line client for `status`, `put`, `get`
- `smoke_test.py`: local end-to-end test with 3 simulated nodes

## Local Simulation (first step)

From `rico/raft_kv_demo`:

```bash
python3 smoke_test.py
```

Manual run:

```bash
python3 coordinator.py
python3 raft_node.py --id pico1 --peers pico1,pico2,pico3
python3 raft_node.py --id pico2 --peers pico1,pico2,pico3
python3 raft_node.py --id pico3 --peers pico1,pico2,pico3
python3 control_client.py status
python3 control_client.py put foo bar
python3 control_client.py get foo
```

## Deploy to 3 Pico 2W boards

1. Flash MicroPython to each Pico 2W.
2. Copy `pico_node_micropython.py` to each board as `main.py`.
3. On each board, set:
- `WIFI_SSID`
- `WIFI_PASSWORD`
- `NODE_ID` (`pico1`, `pico2`, `pico3`)
- `PEERS = ["pico1", "pico2", "pico3"]`
- `COORDINATOR_HOST` (IP of your laptop/server running `coordinator.py`)
4. Start `coordinator.py` on your laptop/server.
5. Reboot all 3 Picos.
6. Use control client:

```bash
python3 control_client.py status
python3 control_client.py put mode auto
python3 control_client.py get mode
```

## Notes

- Writes are acknowledged after commit to majority.
- Reads are served by leader from committed state.
- If leader changes, coordinator retries once using redirect hint.
- This is a learning/demo implementation, not hardened production Raft.

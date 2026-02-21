# IDEAS

Assumption: the 3-node Pico 2W Raft KV cluster is already running and stable (leader election, replicated writes, failover).

## 1) WASM Behavior Packs via Raft (your first idea)

Goal: push behavior changes without reflashing firmware.

- Central server stores signed WASM modules (e.g. `heartbeat.wasm`, `blink_policy.wasm`, `kv_validate.wasm`).
- A Raft key like `runtime/module/current` points to module version + hash.
- Leader commits module metadata and rollout phase; followers replicate and apply.
- Each Pico downloads module bytes from server, verifies hash/signature, and swaps active behavior.
- If module traps/fails health checks, node auto-rolls back to previous known-good module.

Suggested first WASM hooks:
- `heartbeat_interval_ms()`
- `blink_period_ms()`
- `validate_kv(key, value) -> bool`

Success criteria:
- Change blink/heartbeat behavior cluster-wide via one committed update.
- Demonstrate rollback on intentionally bad module.

## 2) Partition-Tolerant Rolling Update Protocol

Goal: safe updates when one Pico is offline or partitioned.

- Add update state machine in Raft metadata: `planned -> staged -> canary -> full -> complete`.
- Require quorum healthy before entering `full`.
- Keep one node as canary for N minutes before promoting rollout.
- Partitioned node catches up log + update version after reconnect, then self-updates.

Success criteria:
- Simulate one disconnected Pico during rollout; reconnect and verify deterministic convergence.
- No split-brain module version across quorum.

## 3) Raft Snapshot + Restore Drill on Tiny Memory

Goal: exercise long-running operations under constrained RAM/flash.

- Add snapshotting after K committed entries.
- Persist snapshot + last included index/term to flash.
- On boot, load snapshot, then replay remaining log tail.
- Support leader-driven snapshot install for lagging follower.

Success criteria:
- Run 10k+ writes in test loop without unbounded log growth.
- Power-cycle one Pico and verify it restores quickly and rejoins cluster.

## 4) Deterministic Rule Engine for Edge Automation

Goal: use the Raft KV store as a deterministic automation brain.

- Store declarative rules in KV (e.g. JSON policy map) and evaluate identically on all nodes.
- Rules control synthetic outputs for now (LED patterns, heartbeat payload shape, accepted key classes).
- Add strict schema validation and versioned rule migrations.
- Keep evaluation deterministic (no wall-clock randomness in committed decisions).

Success criteria:
- Commit policy updates and observe identical behavior transitions across all 3 nodes.
- Reject invalid policy with clear error path and no state divergence.

## Recommended execution order

1. WASM behavior packs (minimal hooks only)
2. Rolling update protocol with canary + rollback
3. Snapshot/restore + log compaction
4. Deterministic rule engine with policy schema validation

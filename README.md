# rico
Rust + Pico microcontroller exercises

# Install

1. Install `pico-sdk` and set `PICO_SDK_PATH` 
2. Install `picotool` is needed for interacting with Pico (e.g. loading binaries).
Note on running `cmake`, I had to add `cmake .. -DCMAKE_POLICY_VERSION_MINIMUM=3.5`.
I was on `cmake` version 4.0. Pre-reqs on macOS was installed with
`brew install gcc pkg-config libusb cmake`
3. Add the build target, `rustup target add thumbv8m.main-none-eabihf`




## Raft KV demo (3x Pico 2W)

A full Raft learning demo is in `raft_kv_demo/` with:
- coordinator relay server
- node implementation
- MicroPython Pico node script
- local smoke test

Start here: `raft_kv_demo/README.md`.

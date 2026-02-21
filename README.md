# rico
Rust + Pico microcontroller exercises

# Install

1. Install `pico-sdk` and set `PICO_SDK_PATH` 
2. Install `picotool` is needed for interacting with Pico (e.g. loading binaries).
Note on running `cmake`, I had to add `cmake .. -DCMAKE_POLICY_VERSION_MINIMUM=3.5`.
I was on `cmake` version 4.0. Pre-reqs on macOS was installed with
`brew install gcc pkg-config libusb cmake`
3. Add the build target, `rustup target add thumbv8m.main-none-eabihf`




## Pico standalone power + Wi-Fi kit

See `pico_standalone_wifi/README.md` for a 3-device bring-up kit (independent power + Wi-Fi auto-connect + heartbeat verification).

# MicroPython standalone Wi-Fi runtime for Pico 2W.
# Copy this file to the board as main.py, alongside node_config.py.

import machine
import network
import socket
import time


LED = machine.Pin("LED", machine.Pin.OUT)


def led(pattern):
    # pattern: "connecting", "online", "error"
    if pattern == "online":
        LED.value(1)
        return
    if pattern == "error":
        for _ in range(5):
            LED.toggle()
            time.sleep_ms(120)
        LED.value(0)
        return
    # connecting
    LED.toggle()


def connect_wifi(ssid, password, timeout_s=20):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        return wlan

    wlan.connect(ssid, password)
    start = time.time()
    while not wlan.isconnected():
        if time.time() - start > timeout_s:
            raise RuntimeError("Wi-Fi timeout")
        led("connecting")
        time.sleep_ms(200)

    led("online")
    print("wifi connected:", wlan.ifconfig())
    return wlan


def heartbeat_tcp(host, port, node_id, interval_s=2, wdt=None):
    # A lightweight liveness loop so each Pico proves it is independent,
    # powered, and online even without attached sensors.
    while True:
        try:
            addr = socket.getaddrinfo(host, port)[0][-1]
            s = socket.socket()
            s.settimeout(2)
            s.connect(addr)
            msg = "hello %s %d\n" % (node_id, time.time())
            s.send(msg.encode())
            s.close()
            led("online")
        except Exception as exc:
            print("heartbeat failed:", exc)
            led("error")
        if wdt is not None:
            wdt.feed()
        time.sleep(interval_s)


def ensure_connected(cfg, wdt=None):
    backoff_s = 1
    while True:
        try:
            wlan = connect_wifi(cfg["WIFI_SSID"], cfg["WIFI_PASSWORD"], cfg.get("WIFI_TIMEOUT_S", 20))
            return wlan
        except Exception as exc:
            print("wifi connect failed:", exc)
            led("error")
            if wdt is not None:
                wdt.feed()
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, 20)


def main():
    machine.freq(125_000_000)
    wdt = machine.WDT(timeout=8000)

    import node_config as cfg

    while True:
        wlan = ensure_connected(cfg.CONFIG, wdt=wdt)
        try:
            heartbeat_tcp(
                cfg.CONFIG["SERVER_HOST"],
                cfg.CONFIG["SERVER_PORT"],
                cfg.CONFIG["NODE_ID"],
                cfg.CONFIG.get("HEARTBEAT_INTERVAL_S", 2),
                wdt=wdt,
            )
        except Exception as exc:
            # If the heartbeat loop exits unexpectedly, attempt full reconnect.
            print("runtime error:", exc)
            led("error")
            wlan.disconnect()
            time.sleep(1)
        finally:
            wdt.feed()


if __name__ == "__main__":
    main()

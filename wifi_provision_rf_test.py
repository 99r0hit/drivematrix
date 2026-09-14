#!/usr/bin/env python3
import argparse
import time
import serial


def read_for(ser, seconds):
    deadline = time.time() + seconds
    lines = []
    while time.time() < deadline:
        raw = ser.readline()
        if raw:
            line = raw.decode("utf-8", errors="replace").rstrip()
            print(line)
            lines.append(line)
    return lines


def send(ser, command, wait=0.5):
    print(f">>> {command}")
    ser.write((command + "\n").encode("utf-8"))
    ser.flush()
    read_for(ser, wait)


def main():
    parser = argparse.ArgumentParser(
        description="DriveMatrix Pi -> Nano -> nRF24 -> RX Wi-Fi credential provisioning test"
    )
    parser.add_argument("port")
    parser.add_argument("rx_id")
    parser.add_argument("ssid")
    parser.add_argument("password")
    args = parser.parse_args()

    if len(args.rx_id) != 12:
        raise SystemExit("RX ID must be 12 hexadecimal characters")
    if len(args.ssid) == 0 or len(args.ssid) > 32:
        raise SystemExit("SSID must be 1-32 characters")
    if len(args.password) > 63:
        raise SystemExit("Password must be 0-63 characters")

    print("==============================")
    print("DriveMatrix Wi-Fi RF Provision Test")
    print("==============================")
    print(f"Nano port: {args.port}")
    print(f"Target RX: {args.rx_id}")
    print()

    with serial.Serial(args.port, 115200, timeout=0.2) as ser:
        # Opening the port resets the Nano. Allow its RF stack to initialize.
        time.sleep(2.5)
        read_for(ser, 1.0)

        send(ser, "WHO")
        send(ser, "DISCOVER", 2.0)
        send(ser, f"TARGET,{args.rx_id}")

        send(ser, f"WIFI_SSID,{args.ssid}", 1.0)
        send(ser, f"WIFI_PASSWORD,{args.password}", 1.0)
        send(ser, "WIFI_COMMIT", 1.0)

        print()
        print("Final RX/Nano output:")
        read_for(ser, 1.0)

    print()
    print("Test sequence complete.")
    print("Expected RX lines:")
    print("  WIFI_RX:1:1/...")
    print("  WIFI_RX:2:1/...")
    print("  WIFI_COMMIT_RECEIVED")
    print("  WIFI_CREDENTIALS_SAVED")
    print("  WIFI_PROVISION_READY")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import subprocess
import sys
import time

from radio_manager_wifi_sync import RadioManager


def get_active_wifi_credentials():
    """Read the currently active Wi-Fi profile from NetworkManager."""
    output = subprocess.check_output(
        [
            "nmcli",
            "-t",
            "-f",
            "ACTIVE,NAME",
            "connection",
            "show",
            "--active",
        ],
        text=True,
    )

    connection_name = None
    for line in output.splitlines():
        if line.startswith("yes:"):
            connection_name = line.split(":", 1)[1]
            break

    if not connection_name:
        raise RuntimeError("No active NetworkManager connection found")

    ssid = subprocess.check_output(
        [
            "nmcli",
            "-t",
            "-g",
            "802-11-wireless.ssid",
            "connection",
            "show",
            "id",
            connection_name,
        ],
        text=True,
    ).strip()

    password = subprocess.check_output(
        [
            "nmcli",
            "-s",
            "-t",
            "-g",
            "802-11-wireless-security.psk",
            "connection",
            "show",
            "id",
            connection_name,
        ],
        text=True,
    ).strip()

    if not ssid:
        raise RuntimeError("Active Wi-Fi connection has no SSID")

    # Empty password is valid for an open network.
    if password == "--":
        password = ""

    return connection_name, ssid, password


def get_ap_credentials():
    """Read the DriveMatrix-AP credentials from NetworkManager."""
    ssid = subprocess.check_output(
        [
            "nmcli", "-t", "-g",
            "802-11-wireless.ssid",
            "connection", "show", "id", "DriveMatrix-AP",
        ],
        text=True,
    ).strip()

    password = subprocess.check_output(
        [
            "sudo", "-n", "nmcli", "-s", "-t", "-g",
            "802-11-wireless-security.psk",
            "connection", "show", "id", "DriveMatrix-AP",
        ],
        text=True,
    ).strip()

    if password == "--":
        password = ""

    if not ssid:
        raise RuntimeError("DriveMatrix-AP has no SSID")

    return ssid, password


def main():
    if len(sys.argv) != 3:
        print(
            "Usage: python3 wifi_sync_test.py "
            "<RADIO_ID> <VEHICLE_NAME>"
        )
        print("Example: python3 wifi_sync_test.py RF-BC035F 'Car 1'")
        raise SystemExit(2)

    radio_id = sys.argv[1].strip()
    vehicle_name = sys.argv[2].strip()

    print()
    print("==============================")
    print("DriveMatrix Pi Wi-Fi Sync Test")
    print("==============================")

    connection_name, ssid, password = get_active_wifi_credentials()
    fallback_ssid, fallback_password = get_ap_credentials()

    print(f"Active connection: {connection_name}")
    print(f"Primary SSID: {ssid}")
    print(f"Primary password: {'SET' if password else 'EMPTY'}")
    print(f"Fallback SSID: {fallback_ssid}")
    print(f"Fallback password: {'SET' if fallback_password else 'EMPTY'}")
    print(f"Radio: {radio_id}")
    print(f"Vehicle: {vehicle_name}")

    manager = RadioManager()

    try:
        radios = manager.discover()
        if radio_id not in radios:
            raise RuntimeError(f"Radio not found: {radio_id}")

        controller = radios[radio_id]

        if not controller.connected:
            if not controller.connect():
                raise RuntimeError(f"Could not connect to {radio_id}")

        # Discovery is required because Nano TARGET validates the RX first.
        manager.discover_receivers()

        if not manager.select_vehicle(radio_id, vehicle_name):
            raise RuntimeError(
                f"Could not select {vehicle_name} on {radio_id}"
            )

        # Allow TARGET to settle before provisioning.
        time.sleep(0.2)

        if not manager.provision_dual_wifi_credentials(
            radio_id,
            ssid,
            password,
            fallback_ssid,
            fallback_password,
        ):
            raise RuntimeError("Dual Wi-Fi provisioning failed")

        # Read Nano responses for a short period so the operator can see
        # WIFI_TX / WIFI_COMMIT output.
        print()
        print("Nano responses:")
        deadline = time.time() + 3.0
        while time.time() < deadline:
            line = controller.read_line()
            if line:
                print(f"  {line}")
            else:
                time.sleep(0.02)

        print()
        print("RESULT: WIFI_SYNC_COMMANDS_SENT")
        print("Reboot the RX and check its Wi-Fi connection output.")

    finally:
        manager.disconnect_all()


if __name__ == "__main__":
    main()

import glob
import serial
import time


BAUD_RATE = 115200
DISCOVERY_TIMEOUT = 1.0


class RadioDevice:
    def __init__(self, port, radio_id):
        self.port = port
        self.radio_id = radio_id

    def __repr__(self):
        return (
            f"RadioDevice("
            f"port={self.port}, "
            f"radio_id={self.radio_id})"
        )


def find_serial_ports():
    """
    Find all likely Arduino serial ports.
    """

    ports = []

    for pattern in (
        "/dev/ttyUSB*",
        "/dev/ttyACM*"
    ):
        ports.extend(glob.glob(pattern))

    return sorted(set(ports))


def identify_radio(port):
    """
    Open one serial port and ask the Nano for its
    permanent RADIO_ID.
    """

    ser = None

    try:

        print(f"Checking {port}...")

        ser = serial.Serial(
            port,
            BAUD_RATE,
            timeout=0.1
        )

        # Opening the Arduino resets it.
        time.sleep(2.0)

        ser.reset_input_buffer()

        ser.write(b"WHO\n")
        ser.flush()

        deadline = time.time() + DISCOVERY_TIMEOUT

        while time.time() < deadline:

            line = ser.readline().decode(
                "ascii",
                errors="replace"
            ).strip()

            if not line:
                continue

            if line.startswith("RADIO_ID:"):

                radio_id = line.split(
                    ":",
                    1
                )[1].strip()

                if radio_id:

                    return RadioDevice(
                        port,
                        radio_id
                    )

        return None

    except Exception as e:

        print(
            f"  Error: {e}"
        )

        return None

    finally:

        if ser:

            try:
                ser.close()
            except Exception:
                pass


def discover_radios():
    """
    Discover every connected Nano radio.
    """

    radios = []

    ports = find_serial_ports()

    print(
        f"Found {len(ports)} serial port(s)"
    )

    for port in ports:

        radio = identify_radio(port)

        if radio:

            radios.append(radio)

            print(
                f"  FOUND: "
                f"{radio.radio_id} "
                f"-> {radio.port}"
            )

        else:

            print(
                f"  Not a DriveMatrix radio: "
                f"{port}"
            )

    return radios


def print_radios(radios):

    print()
    print("==============================")
    print("DriveMatrix Radio Discovery")
    print("==============================")

    if not radios:

        print("No DriveMatrix radios found.")
        return

    print(
        f"Found {len(radios)} DriveMatrix radio(s)"
    )

    print()

    for index, radio in enumerate(radios):

        print(
            f"[{index}] "
            f"Radio ID: {radio.radio_id}"
        )

        print(
            f"    Port: {radio.port}"
        )


if __name__ == "__main__":

    radios = discover_radios()

    print_radios(radios)

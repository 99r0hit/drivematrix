import serial
import time
import threading


class RadioController:
    """
    Controls one Arduino Nano + nRF24 radio.

    Pi -> USB Serial -> Nano -> nRF24 -> RX
    """

    def __init__(self, port, radio_id=None, baudrate=115200):
        self.port = port
        self.radio_id = radio_id
        self.baudrate = baudrate

        self.serial = None
        self.connected = False

        self.sequence = 0

        self.last_tx_time = 0.0
        self.last_ack_time = 0.0

        self.last_ack_sequence = None
        self.last_ack_status = None
        self.last_ack_failsafe = None

        self.lock = threading.Lock()

    # ========================================================
    # CONNECTION
    # ========================================================

    def connect(self):
        """Open the Nano serial port."""

        try:
            self.serial = serial.Serial(
                self.port,
                self.baudrate,
                timeout=0.05
            )

            # Opening the Arduino serial port resets the Nano.
            time.sleep(2.0)

            self.serial.reset_input_buffer()

            self.connected = True

            print(
                f"[RadioController] Connected: "
                f"{self.port}"
            )

            if self.radio_id:
                print(
                    f"[RadioController] Radio ID: "
                    f"{self.radio_id}"
                )

            return True

        except Exception as e:

            print(
                f"[RadioController] Connection failed "
                f"{self.port}: {e}"
            )

            self.connected = False
            self.serial = None

            return False

    # ========================================================
    # DISCONNECT
    # ========================================================

    def disconnect(self):
        """Close the Nano serial connection."""

        with self.lock:

            self.connected = False

            if self.serial:

                try:
                    self.serial.close()
                except Exception:
                    pass

            self.serial = None

        print(
            f"[RadioController] Disconnected: "
            f"{self.port}"
        )

    # ========================================================
    # SERIAL COMMAND
    # ========================================================

    def _send_command(self, command):
        """Send one command to the Nano."""

        with self.lock:

            if not self.connected or not self.serial:
                return False

            try:

                self.serial.write(
                    (command + "\n").encode("ascii")
                )

                self.serial.flush()

                return True

            except Exception as e:

                print(
                    f"[RadioController] "
                    f"Serial write failed: {e}"
                )

                self.connected = False

                return False

    # ========================================================
    # CONTROL
    # ========================================================

    def send_control(self, steering, throttle):
        """
        Send steering and throttle to the Nano.

        Values are signed 16-bit integers.
        """

        steering = int(steering)
        throttle = int(throttle)

        steering = max(
            -32768,
            min(32767, steering)
        )

        throttle = max(
            -32768,
            min(32767, throttle)
        )

        command = (
            f"CONTROL,{steering},{throttle}"
        )

        success = self._send_command(command)

        if success:
            self.last_tx_time = time.time()
            self.sequence = (
                self.sequence + 1
            ) & 0xFFFF

        return success

    # ========================================================
    # SESSION
    # ========================================================

    def send_session_start(self, duration_seconds):
        """Start/reset the selected RX session timer."""
        duration_seconds = int(duration_seconds)
        if duration_seconds <= 0:
            return False
        if duration_seconds > 0xFFFFFFFF:
            duration_seconds = 0xFFFFFFFF

        command = f"SESSION_START,{duration_seconds}"
        return self._send_command(command)

    def send_session_stop(self):
        """Stop the selected RX session timer and request safe outputs."""
        return self._send_command("SESSION_STOP")

    # ========================================================
    # WHO
    # ========================================================

    def who(self):
        """Ask the Nano for its permanent radio ID."""

        if not self._send_command("WHO"):
            return None

        deadline = time.time() + 1.0

        while time.time() < deadline:

            line = self.read_line()

            if not line:
                continue

            if line.startswith("RADIO_ID:"):

                return line.split(
                    ":",
                    1
                )[1].strip()

        return None

    # ========================================================
    # STATUS
    # ========================================================

    def status(self):
        """Ask the Nano for radio status."""

        if not self._send_command("STATUS"):
            return []

        lines = []

        deadline = time.time() + 1.0

        while time.time() < deadline:

            line = self.read_line()

            if not line:
                continue

            lines.append(line)

            if line.startswith("NRF24:"):
                break

        return lines

    # ========================================================
    # PING
    # ========================================================

    def ping(self):
        """Check whether the Nano responds."""

        if not self._send_command("PING"):
            return False

        deadline = time.time() + 1.0

        while time.time() < deadline:

            line = self.read_line()

            if line == "PONG":
                return True

        return False

    # ========================================================
    # READ SERIAL
    # ========================================================

    def read_line(self):
        """Read one line from the Nano."""

        if not self.connected or not self.serial:
            return None

        try:

            if self.serial.in_waiting <= 0:
                return None

            line = self.serial.readline().decode(
                "ascii",
                errors="replace"
            ).strip()

            if not line:
                return None

            self._process_line(line)

            return line

        except Exception as e:

            print(
                f"[RadioController] "
                f"Serial read failed: {e}"
            )

            self.connected = False

            return None

    # ========================================================
    # PROCESS NANO RESPONSE
    # ========================================================

    def _process_line(self, line):

        if line.startswith("ACK_SEQUENCE:"):

            try:
                self.last_ack_sequence = int(
                    line.split(":", 1)[1]
                )
            except ValueError:
                pass

        elif line.startswith("ACK_STATUS:"):

            try:
                self.last_ack_status = int(
                    line.split(":", 1)[1]
                )
            except ValueError:
                pass

        elif line.startswith("ACK_FAILSAFE:"):

            try:
                self.last_ack_failsafe = int(
                    line.split(":", 1)[1]
                )
            except ValueError:
                pass

        elif line == "ACK_VALID":

            self.last_ack_time = time.time()

    # ========================================================
    # TEST
    # ========================================================

    def test(self):

        print()
        print("==============================")
        print("RadioController Test")
        print("==============================")

        if not self.connect():
            print("RESULT: CONNECT_FAILED")
            return False

        print()

        radio_id = self.who()

        print(
            f"WHO: {radio_id}"
        )

        print()

        print("PING:", self.ping())

        print()

        print("STATUS:")

        for line in self.status():
            print(" ", line)

        print()

        print("Sending control...")

        if not self.send_control(1000, 2000):
            print("RESULT: CONTROL_FAILED")
            self.disconnect()
            return False

        time.sleep(0.2)

        # Drain responses from Nano.
        deadline = time.time() + 1.0

        while time.time() < deadline:

            line = self.read_line()

            if line:
                print(" ", line)

            else:
                time.sleep(0.01)

        print()

        print("RESULT: TEST_COMPLETE")

        self.disconnect()

        return True


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":

    controller = RadioController(
        "/dev/ttyUSB0"
    )

    controller.test()

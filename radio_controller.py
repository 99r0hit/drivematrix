import serial
import time
import threading


class RadioController:
    """
    Controls one Arduino Nano + nRF24 radio.

    Pi -> USB Serial -> Nano -> nRF24 -> RX

    The Nano has:
        - permanent RADIO_ID
        - dynamic RX target
        - unique RF control address per RX
    """

    def __init__(
        self,
        port,
        radio_id=None,
        baudrate=115200
    ):
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

        # ----------------------------------------------------
        # Target state
        # ----------------------------------------------------

        self.selected_receiver_id = None

        self.discovered_receivers = {}

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

            if (
                not self.connected
                or not self.serial
            ):
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

    def send_control(
        self,
        steering,
        throttle
    ):
        """
        Send steering and throttle to the currently
        selected RX.

        A target must be selected before control can
        be transmitted.
        """

        if not self.selected_receiver_id:

            print(
                "[RadioController] "
                "CONTROL BLOCKED: no RX target selected"
            )

            return False

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

        success = self._send_command(
            command
        )

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
        """
        Start/reset the RF-authoritative session on the selected RX.

        The Nano sends SESSION_START,<seconds> to the currently
        selected receiver address.
        """
        try:
            duration_seconds = int(duration_seconds)
        except (TypeError, ValueError):
            print(
                "[RadioController] "
                "Invalid session duration"
            )
            return False

        if duration_seconds <= 0:
            print(
                "[RadioController] "
                "Invalid session duration: "
                f"{duration_seconds}"
            )
            return False

        if duration_seconds > 0xFFFFFFFF:
            duration_seconds = 0xFFFFFFFF

        if not self.selected_receiver_id:
            print(
                "[RadioController] "
                "SESSION_START BLOCKED: no RX target selected"
            )
            return False

        command = (
            f"SESSION_START,{duration_seconds}"
        )

        success = self._send_command(command)

        if success:
            print(
                "[RadioController] "
                f"SESSION_START sent: "
                f"{duration_seconds}s -> "
                f"{self.selected_receiver_id}"
            )

        return success

    def send_session_stop(self):
        """
        Stop the RF-authoritative session on the selected RX.
        """
        if not self.selected_receiver_id:
            print(
                "[RadioController] "
                "SESSION_STOP BLOCKED: no RX target selected"
            )
            return False

        success = self._send_command(
            "SESSION_STOP"
        )

        if success:
            print(
                "[RadioController] "
                f"SESSION_STOP sent -> "
                f"{self.selected_receiver_id}"
            )

        return success

    # ========================================================
    # DISCOVER RXs
    # ========================================================

    def discover_receivers(
        self,
        timeout=2.0
    ):
        """
        Ask the Nano to discover all currently reachable RXs.

        Returns:

            {
                "RX_ID": "vehicle name"
            }
        """

        if not self._send_command(
            "DISCOVER"
        ):
            return {}

        discovered = {}

        deadline = (
            time.time() + timeout
        )

        while time.time() < deadline:

            line = self.read_line()

            if not line:
                time.sleep(0.005)
                continue

            # -----------------------------------------------
            # RX_FOUND:<RX_ID>
            # -----------------------------------------------

            if line.startswith(
                "RX_FOUND:"
            ):

                receiver_id = (
                    line.split(
                        ":",
                        1
                    )[1].strip()
                )

                discovered[
                    receiver_id
                ] = ""

                continue

            # -----------------------------------------------
            # VEHICLE_NAME:<name>
            #
            # The Nano prints VEHICLE_NAME immediately after
            # RX_FOUND, so attach it to the most recent RX.
            # -----------------------------------------------

            if line.startswith(
                "VEHICLE_NAME:"
            ):

                vehicle_name = (
                    line.split(
                        ":",
                        1
                    )[1].strip()
                )

                if discovered:

                    last_receiver = (
                        list(discovered.keys())[-1]
                    )

                    discovered[
                        last_receiver
                    ] = vehicle_name

                continue

            # -----------------------------------------------
            # Discovery complete
            # -----------------------------------------------

            if line == (
                "DISCOVERY_COMPLETE"
            ):

                break

        self.discovered_receivers = (
            discovered.copy()
        )

        print(
            f"[RadioController] "
            f"Discovered {len(discovered)} RX(s)"
        )

        for (
            receiver_id,
            vehicle_name
        ) in discovered.items():

            print(
                f"  RX: {receiver_id}"
                f"  Name: {vehicle_name}"
            )

        return discovered

    # ========================================================
    # SELECT RX TARGET
    # ========================================================

    def select_target(
        self,
        receiver_id,
        verify=True,
        timeout=1.0
    ):
        """
        Select one RX as the current control target.

        The Nano changes its nRF24 writing address to the
        unique address belonging to this RX.
        """

        receiver_id = (
            str(receiver_id)
            .strip()
            .upper()
        )

        if len(receiver_id) != 12:

            print(
                "[RadioController] "
                f"Invalid RX ID: {receiver_id}"
            )

            return False

        # ----------------------------------------------------
        # If we have a discovery list, require the target
        # to have been discovered.
        # ----------------------------------------------------

        if (
            self.discovered_receivers
            and receiver_id
            not in self.discovered_receivers
        ):

            print(
                "[RadioController] "
                f"RX not discovered: {receiver_id}"
            )

            return False

        if not self._send_command(
            f"TARGET,{receiver_id}"
        ):
            return False

        if not verify:

            self.selected_receiver_id = (
                receiver_id
            )

            return True

        deadline = (
            time.time() + timeout
        )

        while time.time() < deadline:

            line = self.read_line()

            if not line:
                time.sleep(0.005)
                continue

            expected = (
                f"TARGET_SELECTED:{receiver_id}"
            )

            if line == expected:

                self.selected_receiver_id = (
                    receiver_id
                )

                print(
                    "[RadioController] "
                    f"Target selected: {receiver_id}"
                )

                return True

            if line.startswith(
                "TARGET_NOT_FOUND:"
            ):

                print(
                    "[RadioController] "
                    f"Nano rejected target: "
                    f"{receiver_id}"
                )

                return False

            if line == "TARGET_INVALID":

                print(
                    "[RadioController] "
                    "Nano rejected invalid target"
                )

                return False

        print(
            "[RadioController] "
            f"Target selection timeout: "
            f"{receiver_id}"
        )

        return False

    # ========================================================
    # CLEAR TARGET
    # ========================================================

    def clear_target(self):
        """
        Clear the Pi-side target state.

        The Nano currently does not have a CLEAR_TARGET
        command, so this only prevents the Pi from sending
        control through this controller.
        """

        self.selected_receiver_id = None

    # ========================================================
    # WHO
    # ========================================================

    def who(self):
        """Ask the Nano for its permanent radio ID."""

        if not self._send_command(
            "WHO"
        ):
            return None

        deadline = (
            time.time() + 1.0
        )

        while time.time() < deadline:

            line = self.read_line()

            if not line:
                continue

            if line.startswith(
                "RADIO_ID:"
            ):

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

        if not self._send_command(
            "STATUS"
        ):
            return []

        lines = []

        deadline = (
            time.time() + 1.0
        )

        while time.time() < deadline:

            line = self.read_line()

            if not line:
                continue

            lines.append(line)

            if line.startswith(
                "NRF24:"
            ):
                break

        return lines

    # ========================================================
    # PING
    # ========================================================

    def ping(self):
        """Check whether the Nano responds."""

        if not self._send_command(
            "PING"
        ):
            return False

        deadline = (
            time.time() + 1.0
        )

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

        if (
            not self.connected
            or not self.serial
        ):
            return None

        try:

            if self.serial.in_waiting <= 0:
                return None

            line = (
                self.serial.readline()
                .decode(
                    "ascii",
                    errors="replace"
                )
                .strip()
            )

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

        if line.startswith(
            "ACK_SEQUENCE:"
        ):

            try:

                self.last_ack_sequence = int(
                    line.split(
                        ":",
                        1
                    )[1]
                )

            except ValueError:
                pass

        elif line.startswith(
            "ACK_STATUS:"
        ):

            try:

                self.last_ack_status = int(
                    line.split(
                        ":",
                        1
                    )[1]
                )

            except ValueError:
                pass

        elif line.startswith(
            "ACK_FAILSAFE:"
        ):

            try:

                self.last_ack_failsafe = int(
                    line.split(
                        ":",
                        1
                    )[1]
                )

            except ValueError:
                pass

        elif line == "ACK_VALID":

            self.last_ack_time = (
                time.time()
            )

    # ========================================================
    # TEST
    # ========================================================

    def test(self):
        """
        Test:

        1. Connect
        2. WHO
        3. PING
        4. STATUS
        5. Discover RXs
        6. Select first RX
        7. Send control
        """

        print()
        print("==============================")
        print("RadioController Test")
        print("==============================")

        # ----------------------------------------------------
        # Connect
        # ----------------------------------------------------

        if not self.connect():

            print(
                "RESULT: CONNECT_FAILED"
            )

            return False

        # ----------------------------------------------------
        # WHO
        # ----------------------------------------------------

        print()

        radio_id = self.who()

        print(
            f"WHO: {radio_id}"
        )

        # ----------------------------------------------------
        # PING
        # ----------------------------------------------------

        print()

        print(
            "PING:",
            self.ping()
        )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        print()

        print(
            "STATUS:"
        )

        for line in self.status():

            print(
                " ",
                line
            )

        # ----------------------------------------------------
        # DISCOVERY
        # ----------------------------------------------------

        print()

        print(
            "DISCOVERY:"
        )

        receivers = (
            self.discover_receivers()
        )

        if not receivers:

            print(
                "RESULT: NO_RX"
            )

            self.disconnect()

            return False

        # ----------------------------------------------------
        # Select first RX
        # ----------------------------------------------------

        receiver_id = (
            next(
                iter(receivers)
            )
        )

        print()

        print(
            f"Selecting RX: "
            f"{receiver_id}"
        )

        if not self.select_target(
            receiver_id
        ):

            print(
                "RESULT: TARGET_FAILED"
            )

            self.disconnect()

            return False

        # ----------------------------------------------------
        # Control
        # ----------------------------------------------------

        print()

        print(
            "Sending control..."
        )

        if not self.send_control(
            1000,
            2000
        ):

            print(
                "RESULT: CONTROL_FAILED"
            )

            self.disconnect()

            return False

        time.sleep(
            0.2
        )

        # ----------------------------------------------------
        # Drain Nano responses
        # ----------------------------------------------------

        deadline = (
            time.time() + 1.0
        )

        while time.time() < deadline:

            line = self.read_line()

            if line:

                print(
                    " ",
                    line
                )

            else:

                time.sleep(
                    0.01
                )

        print()

        print(
            "RESULT: TEST_COMPLETE"
        )

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

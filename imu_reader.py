import socket
import struct
import threading


class IMUReader:

    # ============================================================
    # ESP32 TELEMETRY CONFIGURATION
    # ============================================================

    PACKET_TELEMETRY = 4

    # ESP32 TelemetryPacket:
    #
    # uint8_t  type
    # uint32_t timestamp
    # float    accelX
    # float    accelY
    # float    accelZ
    # float    gyroX
    # float    gyroY
    # float    gyroZ
    #
    # Total = 29 bytes
    PACKET_FORMAT = "<BI6f"
    PACKET_SIZE = struct.calcsize(PACKET_FORMAT)

    # ============================================================
    # INITIALIZATION
    # ============================================================

    def __init__(self, port=5005):

        self.port = port
        self.running = False
        self.thread = None
        self.sock = None

        # --------------------------------------------------------
        # Latest IMU acceleration
        # --------------------------------------------------------

        self.accel_x = 0.0
        self.accel_y = 0.0
        self.accel_z = 0.0

        # --------------------------------------------------------
        # Latest gyroscope data
        # --------------------------------------------------------

        self.gyro_x = 0.0
        self.gyro_y = 0.0
        self.gyro_z = 0.0

        # --------------------------------------------------------
        # Latest ESP32 timestamp
        # --------------------------------------------------------

        self.timestamp = 0

        # --------------------------------------------------------
        # Statistics
        # --------------------------------------------------------

        self.packet_count = 0
        self.bad_packet_count = 0

    # ============================================================
    # START
    # ============================================================

    def start(self):

        if self.running:
            return

        self.running = True

        self.thread = threading.Thread(
            target=self._udp_listener,
            daemon=True
        )

        self.thread.start()

        print(
            f"✅ [UDP IMU Reader] Listening on UDP port "
            f"{self.port} | Packet size: {self.PACKET_SIZE} bytes",
            flush=True
        )

    # ============================================================
    # UDP LISTENER
    # ============================================================

    def _udp_listener(self):

        self.sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM
        )

        self.sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )

        self.sock.bind(
            ("0.0.0.0", self.port)
        )

        # Allows stop() to terminate the thread cleanly.
        self.sock.settimeout(1.0)

        print(
            f"📡 [UDP IMU Reader] Socket bound to "
            f"0.0.0.0:{self.port}",
            flush=True
        )

        while self.running:

            try:

                data, _ = self.sock.recvfrom(1024)

                # ------------------------------------------------
                # Packet size check
                # ------------------------------------------------

                if len(data) != self.PACKET_SIZE:

                    self.bad_packet_count += 1

                    continue

                # ------------------------------------------------
                # Unpack ESP32 TelemetryPacket
                # ------------------------------------------------

                values = struct.unpack(
                    self.PACKET_FORMAT,
                    data
                )

                packet_type = values[0]

                # ------------------------------------------------
                # Only accept telemetry packets
                # ------------------------------------------------

                if packet_type != self.PACKET_TELEMETRY:

                    self.bad_packet_count += 1

                    continue

                # ------------------------------------------------
                # Extract packet
                # ------------------------------------------------

                self.timestamp = values[1]

                self.accel_x = values[2]
                self.accel_y = values[3]
                self.accel_z = values[4]

                self.gyro_x = values[5]
                self.gyro_y = values[6]
                self.gyro_z = values[7]

                self.packet_count += 1

            except socket.timeout:

                continue

            except OSError:

                # Socket was closed during stop()
                if not self.running:
                    break

            except Exception as e:

                self.bad_packet_count += 1

                print(
                    f"⚠️ [UDP IMU Reader] Error: {e}",
                    flush=True
                )

        # --------------------------------------------------------
        # Clean socket shutdown
        # --------------------------------------------------------

        if self.sock is not None:

            try:
                self.sock.close()
            except Exception:
                pass

            self.sock = None

    # ============================================================
    # STOP
    # ============================================================

    def stop(self):

        self.running = False

        # Closing the socket wakes recvfrom()
        if self.sock is not None:

            try:
                self.sock.close()
            except Exception:
                pass

            self.sock = None

        # Wait briefly for listener thread
        if (
            self.thread is not None
            and self.thread.is_alive()
        ):

            self.thread.join(timeout=1.5)

        self.thread = None

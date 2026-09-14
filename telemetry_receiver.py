import math
import socket
import struct
import threading
import time


UDP_IP = "0.0.0.0"
UDP_PORT = 5005

PACKET_FORMAT = "<B6sI6f"
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)
PACKET_TELEMETRY = 4

JERK_THRESHOLD = 10000.0
IMPACT_COOLDOWN = 0.25


class TelemetryReceiver:
    """Receive DriveMatrix telemetry and detect high-jerk impacts.

    Step 1 intentionally stops at telemetry reception and impact detection.
    It does not command any G29 FFB and does not modify the RF/control path.
    """

    def __init__(self, on_telemetry=None, on_impact=None):
        self.on_telemetry = on_telemetry
        self.on_impact = on_impact

        self._stop = threading.Event()
        self._thread = None
        self._sock = None
        self._lock = threading.Lock()

        self.packet_count = 0
        self.invalid_packet_count = 0
        self.missed_packets = 0
        self.impact_count = 0
        self.last_impact_time = -float("inf")
        self.previous_by_source = {}

    @staticmethod
    def parse_packet(data):
        if len(data) != PACKET_SIZE:
            raise ValueError(
                f"Wrong packet size: expected {PACKET_SIZE}, got {len(data)}"
            )

        values = struct.unpack(PACKET_FORMAT, data)
        packet_type = values[0]
        receiver_mac = values[1]
        receiver_id = receiver_mac.hex().upper()
        timestamp = values[2]
        accel = values[3:6]
        gyro = values[6:9]

        if packet_type != PACKET_TELEMETRY:
            raise ValueError(f"Wrong packet type: {packet_type}")

        return receiver_id, timestamp, accel, gyro

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop.clear()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.settimeout(0.5)
        self._sock.bind((UDP_IP, UDP_PORT))

        self._thread = threading.Thread(
            target=self._worker,
            name="drivematrix-telemetry",
            daemon=True,
        )
        self._thread.start()

        print(
            f"[Telemetry] Receiver started on UDP {UDP_IP}:{UDP_PORT} "
            f"({PACKET_SIZE}-byte packets)"
        )

    def stop(self):
        self._stop.set()

        if self._thread is not None:
            self._thread.join(timeout=2.0)

        self._thread = None

        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

        print("[Telemetry] Receiver stopped")

    def get_status(self):
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "packet_count": self.packet_count,
                "invalid_packet_count": self.invalid_packet_count,
                "missed_packets": self.missed_packets,
                "impact_count": self.impact_count,
            }

    def _worker(self):
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            except Exception as exc:
                print(f"[Telemetry] Socket error: {exc}")
                continue

            source = addr[0]
            now = time.monotonic()

            try:
                receiver_id, timestamp, accel, gyro = self.parse_packet(data)
            except ValueError as exc:
                with self._lock:
                    self.invalid_packet_count += 1
                print(f"[Telemetry] Invalid packet from {source}: {exc}")
                continue

            with self._lock:
                self.packet_count += 1

            previous = self.previous_by_source.get(receiver_id)
            self.previous_by_source[receiver_id] = (timestamp, accel)

            if previous is None:
                event = {
                    "source_ip": source,
                    "receiver_id": receiver_id,
                    "timestamp": timestamp,
                    "accel": accel,
                    "gyro": gyro,
                    "dt_ms": None,
                    "delta_accel": None,
                    "jerk": None,
                }
                print(
                    f"[Telemetry] Started | RX={receiver_id} | source={source} | "
                    f"timestamp={timestamp} ms | "
                    f"accel=({accel[0]:+.2f}, {accel[1]:+.2f}, {accel[2]:+.2f})"
                )
                if self.on_telemetry is not None:
                    try:
                        self.on_telemetry(event)
                    except Exception as exc:
                        print(f"[Telemetry] Callback error: {exc}")
                continue

            previous_timestamp, previous_accel = previous
            dt_ms = (timestamp - previous_timestamp) & 0xFFFFFFFF
            dt = dt_ms / 1000.0

            if dt_ms > 20:
                expected_packets = round(dt_ms / 20.0)
                missed = max(0, expected_packets - 1)
                with self._lock:
                    self.missed_packets += missed

            if dt <= 0 or dt > 0.1:
                continue

            dx = accel[0] - previous_accel[0]
            dy = accel[1] - previous_accel[1]
            dz = accel[2] - previous_accel[2]

            delta_accel = math.sqrt(dx * dx + dy * dy + dz * dz)
            jerk = delta_accel / dt

            event = {
                "source_ip": source,
                "receiver_id": receiver_id,
                "timestamp": timestamp,
                "accel": accel,
                "gyro": gyro,
                "dt_ms": dt_ms,
                "delta_accel": delta_accel,
                "jerk": jerk,
            }

            if self.on_telemetry is not None:
                try:
                    self.on_telemetry(event)
                except Exception as exc:
                    print(f"[Telemetry] Callback error: {exc}")

            if (
                jerk >= JERK_THRESHOLD
                and now - self.last_impact_time >= IMPACT_COOLDOWN
            ):
                self.last_impact_time = now
                with self._lock:
                    self.impact_count += 1
                    impact_number = self.impact_count

                axis_values = {
                    "FORWARD": abs(dx),
                    "LATERAL": abs(dy),
                    "VERTICAL": abs(dz),
                }
                dominant_axis = max(axis_values, key=axis_values.get)

                impact = {
                    **event,
                    "impact_number": impact_number,
                    "dx": dx,
                    "dy": dy,
                    "dz": dz,
                    "dominant_axis": dominant_axis,
                }

                print(
                    f"[Telemetry] IMPACT #{impact_number} | "
                    f"RX={receiver_id} | source={source} | jerk={jerk:.1f} m/s^3 | "
                    f"delta_accel={delta_accel:.3f} | axis={dominant_axis}"
                )

                if self.on_impact is not None:
                    try:
                        self.on_impact(impact)
                    except Exception as exc:
                        print(f"[Telemetry] Impact callback error: {exc}")


if __name__ == "__main__":
    receiver = TelemetryReceiver()
    receiver.start()

    print("[Telemetry] Running. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        receiver.stop()

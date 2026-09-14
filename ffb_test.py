import socket
import struct
import math
import time

from g29_ffb import G29FFB


# ============================================================
# UDP CONFIGURATION
# ============================================================

UDP_IP = "0.0.0.0"
UDP_PORT = 5005


# ============================================================
# ESP32 TELEMETRY PACKET
#
# struct __attribute__((packed))
# TelemetryPacket {
#     uint8_t  type;
#     uint8_t  receiverMAC[6];
#     uint32_t timestamp;
#     float    accelX;
#     float    accelY;
#     float    accelZ;
#     float    gyroX;
#     float    gyroY;
#     float    gyroZ;
# };
#
# Total = 35 bytes
# ============================================================

PACKET_FORMAT = "<B6sI6f"
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)

PACKET_TELEMETRY = 4


# ============================================================
# IMPACT / FFB CONFIGURATION
# ============================================================

JERK_THRESHOLD = 10000.0

# Minimum time between two impact triggers
IMPACT_COOLDOWN = 0.25

# FFB kick duration
KICK_DURATION = 0.25

# Minimum FFB strength
MIN_FFB = 30.0

# Maximum FFB strength
MAX_FFB = 100.0

# Multiplier used to convert jerk severity into FFB strength
FFB_MULTIPLIER = 50.0


# ============================================================
# PACKET PARSER
# ============================================================

def parse_packet(data):

    if len(data) != PACKET_SIZE:
        raise ValueError(
            f"Wrong packet size: "
            f"expected {PACKET_SIZE}, got {len(data)}"
        )

    values = struct.unpack(
        PACKET_FORMAT,
        data
    )

    packet_type = values[0]
    timestamp = values[1]

    accel = values[2:5]
    gyro = values[5:8]

    if packet_type != PACKET_TELEMETRY:
        raise ValueError(
            f"Wrong packet type: {packet_type}"
        )

    return receiver_id, timestamp, accel, gyro


# ============================================================
# MAIN
# ============================================================

def main():

    print("==============================================")
    print("       ESP32 CRASH DETECTION + G29 FFB")
    print("==============================================")
    print()

    print(f"UDP port          : {UDP_PORT}")
    print(f"Packet size       : {PACKET_SIZE} bytes")
    print(f"Jerk threshold    : {JERK_THRESHOLD:.1f} m/s³")
    print(f"Impact cooldown   : {IMPACT_COOLDOWN:.2f} s")
    print(f"FFB kick duration : {KICK_DURATION:.2f} s")
    print()

    # ========================================================
    # INITIALIZE G29
    # ========================================================

    print("Initializing FFB wheel connection...")

    try:
        ffb = G29FFB()

    except RuntimeError as e:
        print(f"ERROR: {e}")
        return

    print(
        f"Connected to wheel on "
        f"{ffb.dev.path} "
        f"({ffb.dev.name})"
    )

    ffb.disable_autocenter()

    print("FFB ready.")
    print()

    # ========================================================
    # UDP SOCKET
    # ========================================================

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1
    )

    sock.bind(
        (UDP_IP, UDP_PORT)
    )

    print(
        f"Listening for ESP32 telemetry "
        f"on UDP port {UDP_PORT}"
    )

    print()
    print("Crash detection active.")
    print("Press Ctrl+C to stop.")
    print()

    # ========================================================
    # STATE
    # ========================================================

    previous_timestamp = None
    previous_accel = None

    packet_count = 0
    impact_count = 0
    missed_packets = 0

    last_impact_time = -float("inf")

    # --------------------------------------------------------
    # Non-blocking FFB state
    # --------------------------------------------------------

    ffb_active = False
    ffb_start_time = 0.0
    current_ffb_strength = 0.0

    last_status_time = time.monotonic()

    # ========================================================
    # MAIN LOOP
    # ========================================================

    try:

        while True:

            # ------------------------------------------------
            # Receive UDP packet
            # ------------------------------------------------

            data, addr = sock.recvfrom(1024)

            now = time.monotonic()

            try:

                receiver_id, timestamp, accel, gyro = parse_packet(data)

                packet_count += 1

                # =================================================
                # FIRST PACKET
                # =================================================

                if previous_timestamp is None:

                    previous_timestamp = timestamp
                    previous_accel = accel

                    print(
                        f"Telemetry started | RX: {receiver_id} | "
                        f"Timestamp: {timestamp} ms | "
                        f"Accel: "
                        f"X={accel[0]:+.2f}, "
                        f"Y={accel[1]:+.2f}, "
                        f"Z={accel[2]:+.2f}"
                    )

                    continue

                # =================================================
                # ESP32 TIMESTAMP DELTA
                # =================================================

                dt_ms = (
                    timestamp -
                    previous_timestamp
                ) & 0xFFFFFFFF

                dt = dt_ms / 1000.0

                # =================================================
                # PACKET LOSS
                # =================================================

                if dt_ms > 20:

                    expected_packets = round(
                        dt_ms / 20.0
                    )

                    missed = max(
                        0,
                        expected_packets - 1
                    )

                    missed_packets += missed

                # =================================================
                # INVALID TIMESTAMP
                # =================================================

                if dt <= 0 or dt > 0.1:

                    previous_timestamp = timestamp
                    previous_accel = accel

                    continue

                # =================================================
                # ACCELERATION CHANGE
                # =================================================

                dx = accel[0] - previous_accel[0]
                dy = accel[1] - previous_accel[1]
                dz = accel[2] - previous_accel[2]

                delta_accel = math.sqrt(
                    dx * dx +
                    dy * dy +
                    dz * dz
                )

                # =================================================
                # JERK
                # =================================================

                jerk = delta_accel / dt

                # =================================================
                # FFB RELEASE
                #
                # This is checked EVERY packet.
                # No time.sleep(), so UDP keeps running.
                # =================================================

                if ffb_active:

                    if now - ffb_start_time >= KICK_DURATION:

                        ffb.set_hardware_autocenter(0.0)

                        ffb_active = False
                        current_ffb_strength = 0.0

                        print(
                            "FFB kick released"
                        )

                # =================================================
                # IMPACT DETECTION
                # =================================================

                if (
                    jerk >= JERK_THRESHOLD
                    and
                    now - last_impact_time
                    >= IMPACT_COOLDOWN
                ):

                    impact_count += 1

                    last_impact_time = now

                    # =================================================
                    # IMPACT DIRECTION
                    # =================================================

                    if delta_accel > 0:

                        direction_x = dx / delta_accel
                        direction_y = dy / delta_accel
                        direction_z = dz / delta_accel

                    else:

                        direction_x = 0.0
                        direction_y = 0.0
                        direction_z = 0.0

                    # =================================================
                    # DOMINANT IMPACT AXIS
                    # =================================================

                    axis_values = {
                        "FORWARD": abs(dx),
                        "LATERAL": abs(dy),
                        "VERTICAL": abs(dz)
                    }

                    dominant_axis = max(
                        axis_values,
                        key=axis_values.get
                    )

                    # =================================================
                    # IMPACT SEVERITY
                    # =================================================

                    severity = (
                        jerk /
                        JERK_THRESHOLD
                    )

                    impact_torque = (
                        severity *
                        FFB_MULTIPLIER
                    )

                    impact_torque = min(
                        MAX_FFB,
                        max(
                            MIN_FFB,
                            impact_torque
                        )
                    )

                    # =================================================
                    # PRINT IMPACT
                    # =================================================

                    print()
                    print(
                        "=============================================="
                    )
                    print(
                        "             CRASH IMPACT DETECTED"
                    )
                    print(
                        "=============================================="
                    )

                    print(
                        f"Impact #       : {impact_count}"
                    )

                    print(
                        f"ESP timestamp  : "
                        f"{timestamp} ms"
                    )

                    print(
                        f"dt             : "
                        f"{dt_ms} ms"
                    )

                    print(
                        f"Delta accel    : "
                        f"{delta_accel:.3f} m/s²"
                    )

                    print(
                        f"Jerk           : "
                        f"{jerk:.1f} m/s³"
                    )

                    print(
                        f"Severity       : "
                        f"{severity:.2f}x"
                    )

                    print(
                        f"Impact vector  : "
                        f"X={dx:+.3f} "
                        f"Y={dy:+.3f} "
                        f"Z={dz:+.3f}"
                    )

                    print(
                        f"Direction      : "
                        f"X={direction_x:+.2f} "
                        f"Y={direction_y:+.2f} "
                        f"Z={direction_z:+.2f}"
                    )

                    print(
                        f"Dominant axis  : "
                        f"{dominant_axis}"
                    )

                    print(
                        f"FFB strength   : "
                        f"{impact_torque:.1f}%"
                    )

                    print(
                        "=============================================="
                    )

                    # =================================================
                    # START FFB KICK
                    # =================================================

                    ffb.set_hardware_autocenter(
                        impact_torque
                    )

                    ffb_active = True
                    ffb_start_time = now
                    current_ffb_strength = impact_torque

                # =================================================
                # SAVE SAMPLE
                # =================================================

                previous_timestamp = timestamp
                previous_accel = accel

                # =================================================
                # STATUS
                # =================================================

                if (
                    now - last_status_time
                    >= 1.0
                ):

                    print(
                        f"RX OK | "
                        f"packets={packet_count} | "
                        f"dt={dt_ms} ms | "
                        f"jerk={jerk:.1f} m/s³ | "
                        f"FFB="
                        f"{current_ffb_strength:.1f}% | "
                        f"impacts={impact_count}"
                    )

                    last_status_time = now

            except Exception as e:

                print(
                    f"Packet error from "
                    f"{addr}: {e}"
                )

    except KeyboardInterrupt:

        print()
        print("Stopping crash detector...")

    finally:

        # ========================================================
        # ALWAYS RELEASE FFB
        # ========================================================

        try:
            ffb.set_hardware_autocenter(0.0)
        except Exception:
            pass

        try:
            ffb.stop()
        except Exception:
            pass

        sock.close()

        print()
        print("==============================================")
        print("              TEST SUMMARY")
        print("==============================================")
        print(
            f"Packets received : "
            f"{packet_count}"
        )
        print(
            f"Missed packets   : "
            f"{missed_packets}"
        )
        print(
            f"Impacts detected : "
            f"{impact_count}"
        )
        print("==============================================")


if __name__ == "__main__":
    main()

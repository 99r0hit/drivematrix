import socket
import struct
import math
import time


# ============================================================
# UDP CONFIGURATION
# ============================================================

UDP_IP = "0.0.0.0"
UDP_PORT = 5005


# ============================================================
# ESP32 TELEMETRY PACKET
# ============================================================

PACKET_FORMAT = "<BI6f"
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)

PACKET_TELEMETRY = 4


# ============================================================
# IMPACT DETECTION
# ============================================================

# Starting threshold only.
#
# At 50 Hz:
#   15 m/s² change in acceleration over 20 ms
#   = 750 m/s³ jerk
#
JERK_THRESHOLD = 750.0

# Minimum time between detected impacts
IMPACT_COOLDOWN = 0.25


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

    return timestamp, accel, gyro


# ============================================================
# MAIN
# ============================================================

def main():

    print("==============================================")
    print("        ESP32 IMPACT DETECTION TEST")
    print("==============================================")
    print()
    print(f"UDP port          : {UDP_PORT}")
    print(f"Packet size       : {PACKET_SIZE} bytes")
    print(f"Jerk threshold    : {JERK_THRESHOLD:.1f} m/s³")
    print(f"Impact cooldown   : {IMPACT_COOLDOWN:.2f} s")
    print()
    print("FFB is NOT enabled.")
    print("This test only detects and logs impacts.")
    print()
    print("Waiting for ESP32...")
    print()

    # --------------------------------------------------------
    # UDP
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # State
    # --------------------------------------------------------

    previous_timestamp = None
    previous_accel = None

    last_impact_time = -float("inf")

    packet_count = 0
    impact_count = 0
    missed_packets = 0

    last_status_time = time.monotonic()

    try:

        while True:

            data, addr = sock.recvfrom(1024)

            try:

                timestamp, accel, gyro = parse_packet(data)

                packet_count += 1

                # ------------------------------------------------
                # FIRST PACKET
                # ------------------------------------------------

                if previous_timestamp is None:

                    previous_timestamp = timestamp
                    previous_accel = accel

                    print(
                        f"Telemetry started | "
                        f"timestamp={timestamp} ms | "
                        f"accel=("
                        f"{accel[0]:+.3f}, "
                        f"{accel[1]:+.3f}, "
                        f"{accel[2]:+.3f}"
                        f")"
                    )

                    continue

                # ------------------------------------------------
                # TIMESTAMP
                # ------------------------------------------------

                dt_ms = (
                    timestamp - previous_timestamp
                ) & 0xFFFFFFFF

                dt = dt_ms / 1000.0

                # ------------------------------------------------
                # Detect missed packets
                # ------------------------------------------------

                if dt_ms > 20:

                    expected = round(
                        dt_ms / 20.0
                    )

                    missed = max(
                        0,
                        expected - 1
                    )

                    missed_packets += missed

                # ------------------------------------------------
                # Ignore invalid dt
                # ------------------------------------------------

                if dt <= 0 or dt > 0.1:

                    previous_timestamp = timestamp
                    previous_accel = accel

                    continue

                # ------------------------------------------------
                # ACCELERATION DIFFERENCE
                # ------------------------------------------------

                dx = accel[0] - previous_accel[0]
                dy = accel[1] - previous_accel[1]
                dz = accel[2] - previous_accel[2]

                delta_accel = math.sqrt(
                    dx * dx +
                    dy * dy +
                    dz * dz
                )

                # ------------------------------------------------
                # JERK
                # ------------------------------------------------

                jerk = delta_accel / dt

                now = time.monotonic()

                # ------------------------------------------------
                # IMPACT DETECTION
                # ------------------------------------------------

                if (
                    jerk >= JERK_THRESHOLD
                    and
                    now - last_impact_time
                    >= IMPACT_COOLDOWN
                ):

                    impact_count += 1
                    last_impact_time = now

                    # --------------------------------------------
                    # NORMALIZED IMPACT VECTOR
                    # --------------------------------------------

                    if delta_accel > 0:

                        direction_x = dx / delta_accel
                        direction_y = dy / delta_accel
                        direction_z = dz / delta_accel

                    else:

                        direction_x = 0
                        direction_y = 0
                        direction_z = 0

                    # --------------------------------------------
                    # DOMINANT AXIS
                    # --------------------------------------------

                    values = {
                        "FORWARD": abs(dx),
                        "LATERAL": abs(dy),
                        "VERTICAL": abs(dz),
                    }

                    dominant_axis = max(
                        values,
                        key=values.get
                    )

                    # --------------------------------------------
                    # LOG IMPACT
                    # --------------------------------------------

                    print()
                    print(
                        "=============================================="
                    )
                    print(
                        "             IMPACT DETECTED"
                    )
                    print(
                        "=============================================="
                    )

                    print(
                        f"Impact #       : {impact_count}"
                    )

                    print(
                        f"ESP timestamp  : {timestamp} ms"
                    )

                    print(
                        f"dt             : {dt_ms} ms"
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
                        f"Delta vector   : "
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
                        "=============================================="
                    )
                    print()

                # ------------------------------------------------
                # SAVE SAMPLE
                # ------------------------------------------------

                previous_timestamp = timestamp
                previous_accel = accel

                # ------------------------------------------------
                # STATUS
                # ------------------------------------------------

                if (
                    now - last_status_time
                    >= 1.0
                ):

                    print(
                        f"RX OK | "
                        f"packets={packet_count} | "
                        f"dt={dt_ms} ms | "
                        f"accel=("
                        f"{accel[0]:+.2f}, "
                        f"{accel[1]:+.2f}, "
                        f"{accel[2]:+.2f}"
                        f") | "
                        f"jerk={jerk:.1f} m/s³ | "
                        f"impacts={impact_count}"
                    )

                    last_status_time = now

            except Exception as e:

                print(
                    f"Packet error from {addr}: {e}"
                )

    except KeyboardInterrupt:

        print()
        print("Stopping...")

    finally:

        sock.close()

        print()
        print("==============================================")
        print("TEST SUMMARY")
        print("==============================================")
        print(f"Packets received : {packet_count}")
        print(f"Missed packets   : {missed_packets}")
        print(f"Impacts detected : {impact_count}")
        print("==============================================")


if __name__ == "__main__":
    main()

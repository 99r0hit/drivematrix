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
# C++:
#
# struct __attribute__((packed)) TelemetryPacket {
#     uint8_t type;
#     uint32_t timestamp;
#     float accelX;
#     float accelY;
#     float accelZ;
#     float gyroX;
#     float gyroY;
#     float gyroZ;
# };
#
# Total:
#   1 + 4 + (6 * 4) = 29 bytes
#
# Python:
#   B = uint8_t
#   I = uint32_t
#   6f = six floats
# ============================================================

PACKET_FORMAT = "<BI6f"
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)

PACKET_TELEMETRY = 4


# ============================================================
# IMPACT DETECTION SETTINGS
# ============================================================

# ESP32 currently sends telemetry every 20 ms = 50 Hz.
#
# Jerk = change in acceleration / time.
#
# 15 m/s² change over 20 ms:
#
#   15 / 0.020 = 750 m/s³
#
# This is therefore the starting threshold.
JERK_THRESHOLD = 750.0

# Maximum FFB strength
MAX_FFB = 100.0

# Converts jerk to FFB percentage.
#
# 750 m/s³  -> ~50%
# 1500 m/s³ -> ~100%
FFB_SCALE = 0.0667

# Duration of impact FFB
KICK_DURATION = 0.30

# Prevent repeated triggers from the same impact
IMPACT_COOLDOWN = 0.25

# Valid ESP32 timestamp interval
MIN_DT = 0.001
MAX_DT = 0.100


# ============================================================
# PARSE TELEMETRY PACKET
# ============================================================

def parse_telemetry_packet(data: bytes):
    """
    Parse the exact 29-byte ESP32 TelemetryPacket.
    """

    if len(data) != PACKET_SIZE:
        raise ValueError(
            f"Invalid packet size: expected {PACKET_SIZE}, "
            f"got {len(data)}"
        )

    values = struct.unpack(PACKET_FORMAT, data)

    packet_type = values[0]
    timestamp = values[1]

    accel = values[2:5]
    gyro = values[5:8]

    if packet_type != PACKET_TELEMETRY:
        raise ValueError(
            f"Invalid packet type: expected "
            f"{PACKET_TELEMETRY}, got {packet_type}"
        )

    return {
        "type": packet_type,
        "timestamp": timestamp,
        "accel": accel,
        "gyro": gyro,
    }


# ============================================================
# MAIN
# ============================================================

def run_udp_crash_detection_ffb():

    # --------------------------------------------------------
    # INITIALIZE G29
    # --------------------------------------------------------

    print("Initializing FFB wheel connection...")

    try:
        ffb = G29FFB()

    except RuntimeError as e:
        print(f"FFB initialization failed: {e}")
        return

    print(
        f"Connected to wheel on "
        f"{ffb.dev.path} ({ffb.dev.name})"
    )

    ffb.disable_autocenter()


    # --------------------------------------------------------
    # CREATE UDP SOCKET
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
    # STARTUP INFORMATION
    # --------------------------------------------------------

    print()
    print(
        f"Listening for ESP32 telemetry "
        f"on UDP port {UDP_PORT}"
    )

    print(
        f"Expected packet size : "
        f"{PACKET_SIZE} bytes"
    )

    print(
        f"Jerk threshold       : "
        f"{JERK_THRESHOLD:.1f} m/s^3"
    )

    print(
        f"FFB kick duration    : "
        f"{KICK_DURATION:.2f} s"
    )

    print()
    print(
        "Impact detection active. "
        "Press Ctrl+C to stop."
    )
    print()


    # --------------------------------------------------------
    # STATE VARIABLES
    # --------------------------------------------------------

    last_accel = None
    last_timestamp = None

    last_impact_time = -float("inf")

    # Time at which the current FFB effect should end
    ffb_until = 0.0

    # Statistics
    packet_count = 0
    bad_packet_count = 0
    missed_packet_count = 0

    # Status display
    last_status_time = time.monotonic()

    # Last calculated values
    last_jerk = 0.0
    last_delta_accel = 0.0
    last_dt = 0.0


    # ========================================================
    # RECEIVE LOOP
    # ========================================================

    try:

        while True:

            # ------------------------------------------------
            # RECEIVE UDP PACKET
            # ------------------------------------------------

            data, addr = sock.recvfrom(1024)

            try:

                pkt = parse_telemetry_packet(data)

                packet_count += 1

                timestamp = pkt["timestamp"]
                current_accel = pkt["accel"]
                current_gyro = pkt["gyro"]


                # ====================================================
                # FIRST PACKET
                # ====================================================

                if last_timestamp is None:

                    last_timestamp = timestamp
                    last_accel = current_accel

                    print(
                        f"Telemetry started | "
                        f"Timestamp: {timestamp} ms | "
                        f"Accel: "
                        f"X={current_accel[0]:+.2f}, "
                        f"Y={current_accel[1]:+.2f}, "
                        f"Z={current_accel[2]:+.2f}"
                    )

                    continue


                # ====================================================
                # ESP32 TIMESTAMP DIFFERENCE
                # ====================================================
                #
                # uint32_t millis() wraps after ~49.7 days.
                # & 0xFFFFFFFF handles the wrap.
                # ====================================================

                dt_ms = (
                    timestamp - last_timestamp
                ) & 0xFFFFFFFF

                dt = dt_ms / 1000.0

                last_dt = dt


                # ====================================================
                # DETECT MISSED TELEMETRY PACKETS
                # ====================================================

                if dt_ms > 20:

                    expected_packets = max(
                        1,
                        round(dt_ms / 20.0)
                    )

                    missed = expected_packets - 1

                    if missed > 0:
                        missed_packet_count += missed


                # ====================================================
                # INVALID TIMESTAMP INTERVAL
                # ====================================================

                if dt < MIN_DT or dt > MAX_DT:

                    last_timestamp = timestamp
                    last_accel = current_accel

                    continue


                # ====================================================
                # ACCELERATION CHANGE
                # ====================================================

                dx = (
                    current_accel[0]
                    - last_accel[0]
                )

                dy = (
                    current_accel[1]
                    - last_accel[1]
                )

                dz = (
                    current_accel[2]
                    - last_accel[2]
                )


                delta_accel = math.sqrt(
                    dx * dx +
                    dy * dy +
                    dz * dz
                )

                last_delta_accel = delta_accel


                # ====================================================
                # ACTUAL JERK
                # ====================================================
                #
                # jerk = Δacceleration / Δtime
                #
                # Units:
                # m/s² / s = m/s³
                # ====================================================

                jerk = delta_accel / dt

                last_jerk = jerk


                # ====================================================
                # CURRENT TIME
                # ====================================================

                now = time.monotonic()


                # ====================================================
                # END FFB EFFECT WHEN TIMER EXPIRES
                # ====================================================

                if (
                    ffb_until > 0
                    and now >= ffb_until
                ):

                    ffb.set_hardware_autocenter(0.0)

                    ffb_until = 0.0


                # ====================================================
                # IMPACT DETECTION
                # ====================================================

                if (
                    jerk >= JERK_THRESHOLD
                    and
                    (
                        now - last_impact_time
                        >= IMPACT_COOLDOWN
                    )
                ):

                    last_impact_time = now


                    # ------------------------------------------------
                    # FFB STRENGTH
                    # ------------------------------------------------

                    impact_torque = min(
                        MAX_FFB,
                        jerk * FFB_SCALE
                    )


                    # ------------------------------------------------
                    # IMPACT DIRECTION
                    # ------------------------------------------------

                    if delta_accel > 0:

                        dir_x = dx / delta_accel
                        dir_y = dy / delta_accel
                        dir_z = dz / delta_accel

                    else:

                        dir_x = 0.0
                        dir_y = 0.0
                        dir_z = 0.0


                    # ------------------------------------------------
                    # DETERMINE DOMINANT AXIS
                    # ------------------------------------------------

                    abs_x = abs(dx)
                    abs_y = abs(dy)
                    abs_z = abs(dz)

                    if (
                        abs_x >= abs_y
                        and abs_x >= abs_z
                    ):

                        dominant_axis = "FORWARD"

                    elif (
                        abs_y >= abs_x
                        and abs_y >= abs_z
                    ):

                        dominant_axis = "LATERAL"

                    else:

                        dominant_axis = "VERTICAL"


                    # ------------------------------------------------
                    # PRINT IMPACT INFORMATION
                    # ------------------------------------------------

                    print()
                    print(
                        "========== IMPACT DETECTED =========="
                    )

                    print(
                        f"Timestamp     : "
                        f"{timestamp} ms"
                    )

                    print(
                        f"dt            : "
                        f"{dt * 1000:.1f} ms"
                    )

                    print(
                        f"Acceleration  : "
                        f"X={current_accel[0]:+.2f}, "
                        f"Y={current_accel[1]:+.2f}, "
                        f"Z={current_accel[2]:+.2f} "
                        f"m/s²"
                    )

                    print(
                        f"Delta Accel   : "
                        f"{delta_accel:.2f} m/s²"
                    )

                    print(
                        f"Jerk          : "
                        f"{jerk:.1f} m/s³"
                    )

                    print(
                        f"Direction     : "
                        f"X={dir_x:+.2f}, "
                        f"Y={dir_y:+.2f}, "
                        f"Z={dir_z:+.2f}"
                    )

                    print(
                        f"Dominant Axis : "
                        f"{dominant_axis}"
                    )

                    print(
                        f"FFB Strength  : "
                        f"{impact_torque:.1f}%"
                    )

                    print(
                        "====================================="
                    )

                    print()


                    # ------------------------------------------------
                    # START NON-BLOCKING FFB PULSE
                    # ------------------------------------------------

                    ffb.set_hardware_autocenter(
                        impact_torque
                    )

                    ffb_until = (
                        now + KICK_DURATION
                    )


                # ====================================================
                # SAVE CURRENT SAMPLE
                # ====================================================

                last_accel = current_accel
                last_timestamp = timestamp


                # ====================================================
                # PERIODIC STATUS
                # ====================================================

                if (
                    now - last_status_time
                    >= 1.0
                ):

                    print(
                        f"RX OK | "
                        f"packets={packet_count} | "
                        f"timestamp={timestamp} | "
                        f"dt={dt * 1000:.1f} ms | "
                        f"accel=("
                        f"{current_accel[0]:+.2f}, "
                        f"{current_accel[1]:+.2f}, "
                        f"{current_accel[2]:+.2f}"
                        f") | "
                        f"jerk={jerk:.1f} m/s³"
                    )

                    last_status_time = now


            except Exception as ex:

                bad_packet_count += 1

                print(
                    f"Packet error from {addr}: "
                    f"{ex} "
                    f"(length={len(data)})"
                )


    # ========================================================
    # CLEAN SHUTDOWN
    # ========================================================

    except KeyboardInterrupt:

        print()
        print("Stopping listener...")


    finally:

        try:
            ffb.set_hardware_autocenter(0.0)
        except Exception:
            pass

        try:
            ffb.stop()
        except Exception:
            pass

        try:
            sock.close()
        except Exception:
            pass

        print()
        print("FFB cleared.")

        print(
            f"Packets received : "
            f"{packet_count}"
        )

        print(
            f"Bad packets      : "
            f"{bad_packet_count}"
        )

        print(
            f"Missed packets   : "
            f"{missed_packet_count}"
        )

        print("Done.")


# ============================================================
# PROGRAM ENTRY
# ============================================================

if __name__ == "__main__":
    run_udp_crash_detection_ffb()

import time
import glob
import struct
import serial
import pygame
import math
import evdev

from ffb_state import VehicleState
from ffb_effects import FFBEffectsEngine
from ffb_mixer import FFBMixer
from g29_ffb import G29FFB
from imu_reader import IMUReader
from haptic_engine import HapticEngine


# ============================================================
# PACKET CONFIGURATION
# ============================================================

PACKET_CONTROL = 1
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
# CRASH / IMPACT DETECTION
# ============================================================

# Proven working value from standalone crash test.
JERK_THRESHOLD = 10000.0

# Prevent multiple detections from the same impact.
IMPACT_COOLDOWN = 0.25

# Duration of crash FFB kick.
KICK_DURATION = 0.25

# Minimum impact FFB.
MIN_IMPACT_FFB = 30.0

# Maximum impact FFB.
MAX_IMPACT_FFB = 100.0

# Proven scaling from standalone test:
#
# 10000 jerk -> 50%
# 15000 jerk -> 75%
# 20000 jerk -> 100%
#
IMPACT_FFB_SCALE = 50.0


# ============================================================
# EXISTING PROJECT OBJECTS
# ============================================================

state = VehicleState()
effects = FFBEffectsEngine()
mixer = FFBMixer()


# ============================================================
# RF NANO
# ============================================================

def find_rf_port():

    ports = sorted(
        glob.glob("/dev/ttyUSB*") +
        glob.glob("/dev/ttyACM*")
    )

    return ports[0] if ports else None


# ============================================================
# G29 EVENT DEVICE
# ============================================================

def get_wheel_event_device():

    # Prefer Logitech wheel with EV_FF capability.
    for dev_path in sorted(
        glob.glob("/dev/input/event*")
    ):

        try:

            dev = evdev.InputDevice(
                dev_path
            )

            if (
                evdev.ecodes.EV_FF in
                dev.capabilities()
                and
                "Logitech" in dev.name
            ):

                return dev

        except Exception:
            pass

    # Fallback to any EV_FF device.
    for dev_path in sorted(
        glob.glob("/dev/input/event*")
    ):

        try:

            dev = evdev.InputDevice(
                dev_path
            )

            if (
                evdev.ecodes.EV_FF in
                dev.capabilities()
            ):

                return dev

        except Exception:
            pass

    return None


# ============================================================
# MAIN DRIVE LOOP
# ============================================================

def run_loop(
    is_active_callback,
    get_settings,
    is_autocenter_enabled,
    get_haptic_settings=None
):

    # ========================================================
    # RF NANO SERIAL
    # ========================================================

    port = find_rf_port()

    if not port:

        print(
            "❌ Error: No RF Nano found",
            flush=True
        )

        return

    try:

        ser = serial.Serial(
            port,
            115200,
            timeout=0
        )

        print(
            f"✅ Connected to RF Nano on {port}",
            flush=True
        )

    except Exception as e:

        print(
            f"❌ Serial Error: {e}",
            flush=True
        )

        return

    # ========================================================
    # G29
    # ========================================================

    wheel_dev = get_wheel_event_device()

    if not wheel_dev:

        print(
            "❌ Error: G29 EV_FF Event Device not found!",
            flush=True
        )

        ser.close()

        return

    ffb_driver = G29FFB(wheel_dev)

    # ========================================================
    # IMU
    # ========================================================

    imu_hw = IMUReader(
        port=5005
    )

    imu_hw.start()

    haptics = HapticEngine(
        imu_hw
    )

    haptics.calibrate_resting_offsets()

    # ========================================================
    # IMPORTANT:
    #
    # IMUReader owns the UDP socket.
    #
    # DO NOT access imu_hw.sock here.
    #
    # app/minimal_drive reads the latest telemetry directly
    # from IMUReader.
    # ========================================================

    # ========================================================
    # PYGAME
    # ========================================================

    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:

        print(
            "❌ Error: No steering wheel found!",
            flush=True
        )

        imu_hw.stop()
        ser.close()

        return

    js = pygame.joystick.Joystick(0)
    js.init()

    # ========================================================
    # CRASH DETECTION STATE
    # ========================================================

    previous_timestamp = None
    previous_accel = None

    last_impact_time = -float("inf")

    impact_count = 0

    # ========================================================
    # NON-BLOCKING CRASH FFB STATE
    # ========================================================

    crash_ffb_active = False

    crash_ffb_until = 0.0

    crash_ffb_strength = 0.0

    # ========================================================
    # MAIN LOOP
    # ========================================================

    try:

        while True:

            active = is_active_callback()

            # =================================================
            # SESSION INACTIVE
            # =================================================

            if not active:

                ffb_driver.set_hardware_autocenter(
                    0
                )

                crash_ffb_active = False
                crash_ffb_strength = 0.0

                stop_packet = struct.pack(
                    "<Bhh",
                    PACKET_CONTROL,
                    0,
                    0
                )

                ser.reset_output_buffer()

                ser.write(
                    stop_packet
                )

                time.sleep(0.05)

                continue

            # =================================================
            # HAPTIC SETTINGS
            # =================================================

            if get_haptic_settings:

                h_cfg = get_haptic_settings()

                haptics.update_tuning(

                    grip_thresh=h_cfg.get(
                        "grip_threshold"
                    ),

                    bump_sens=h_cfg.get(
                        "bump_sensitivity"
                    ),

                    centering_gain=h_cfg.get(
                        "centering_gain"
                    ),

                    alpha=0.45
                )

            # =================================================
            # CRASH DETECTION
            #
            # IMUReader has already received the UDP packet.
            # We calculate jerk from the latest samples here.
            # =================================================

            timestamp = imu_hw.timestamp

            accel = (
                imu_hw.accel_x,
                imu_hw.accel_y,
                imu_hw.accel_z
            )

            if timestamp != 0:

                # ------------------------------------------------
                # First telemetry sample
                # ------------------------------------------------

                if previous_timestamp is None:

                    previous_timestamp = timestamp

                    previous_accel = accel

                # ------------------------------------------------
                # New telemetry sample
                # ------------------------------------------------

                elif timestamp != previous_timestamp:

                    dt_ms = (
                        timestamp -
                        previous_timestamp
                    ) & 0xFFFFFFFF

                    dt = dt_ms / 1000.0

                    # ------------------------------------------------
                    # Ignore invalid timing
                    # ------------------------------------------------

                    if (
                        dt > 0.0
                        and
                        dt <= 0.1
                    ):

                        dx = (
                            accel[0] -
                            previous_accel[0]
                        )

                        dy = (
                            accel[1] -
                            previous_accel[1]
                        )

                        dz = (
                            accel[2] -
                            previous_accel[2]
                        )

                        # --------------------------------------------
                        # Acceleration delta magnitude
                        # --------------------------------------------

                        delta_accel = math.sqrt(
                            dx * dx +
                            dy * dy +
                            dz * dz
                        )

                        # --------------------------------------------
                        # Jerk
                        # --------------------------------------------

                        jerk = (
                            delta_accel /
                            dt
                        )

                        now = time.monotonic()

                        # ============================================
                        # RELEASE CRASH FFB
                        # ============================================

                        if (
                            crash_ffb_active
                            and
                            now >= crash_ffb_until
                        ):

                            crash_ffb_active = False

                            crash_ffb_strength = 0.0

                        # ============================================
                        # IMPACT DETECTION
                        # ============================================

                        if (
                            jerk >= JERK_THRESHOLD
                            and
                            (
                                now -
                                last_impact_time
                            ) >= IMPACT_COOLDOWN
                        ):

                            impact_count += 1

                            last_impact_time = now

                            # ----------------------------------------
                            # Calculate FFB strength
                            # ----------------------------------------

                            impact_torque = min(
                                MAX_IMPACT_FFB,
                                max(
                                    MIN_IMPACT_FFB,
                                    (
                                        jerk /
                                        JERK_THRESHOLD
                                    ) *
                                    IMPACT_FFB_SCALE
                                )
                            )

                            # ----------------------------------------
                            # Impact direction
                            #
                            # Calculated only for diagnostics.
                            # No directional FFB is used.
                            # ----------------------------------------

                            if delta_accel > 0.0:

                                direction_x = (
                                    dx /
                                    delta_accel
                                )

                                direction_y = (
                                    dy /
                                    delta_accel
                                )

                                direction_z = (
                                    dz /
                                    delta_accel
                                )

                            else:

                                direction_x = 0.0
                                direction_y = 0.0
                                direction_z = 0.0

                            # ----------------------------------------
                            # Dominant axis
                            # ----------------------------------------

                            axis_values = {
                                "FORWARD": abs(dx),
                                "LATERAL": abs(dy),
                                "VERTICAL": abs(dz)
                            }

                            dominant_axis = max(
                                axis_values,
                                key=axis_values.get
                            )

                            # ----------------------------------------
                            # Console
                            # ----------------------------------------

                            print(
                                "\n"
                                "==============================================",
                                flush=True
                            )

                            print(
                                "          CRASH IMPACT DETECTED",
                                flush=True
                            )

                            print(
                                "==============================================",
                                flush=True
                            )

                            print(
                                f"Impact #      : "
                                f"{impact_count}",
                                flush=True
                            )

                            print(
                                f"Timestamp     : "
                                f"{timestamp} ms",
                                flush=True
                            )

                            print(
                                f"dt            : "
                                f"{dt_ms} ms",
                                flush=True
                            )

                            print(
                                f"Delta accel   : "
                                f"{delta_accel:.3f} m/s²",
                                flush=True
                            )

                            print(
                                f"Jerk          : "
                                f"{jerk:.1f} m/s³",
                                flush=True
                            )

                            print(
                                f"Impact vector : "
                                f"X={dx:+.3f} "
                                f"Y={dy:+.3f} "
                                f"Z={dz:+.3f}",
                                flush=True
                            )

                            print(
                                f"Direction     : "
                                f"X={direction_x:+.2f} "
                                f"Y={direction_y:+.2f} "
                                f"Z={direction_z:+.2f}",
                                flush=True
                            )

                            print(
                                f"Dominant axis : "
                                f"{dominant_axis}",
                                flush=True
                            )

                            print(
                                f"FFB kick      : "
                                f"{impact_torque:.1f}%",
                                flush=True
                            )

                            print(
                                "==============================================",
                                flush=True
                            )

                            # ========================================
                            # START NON-BLOCKING FFB KICK
                            # ========================================

                            ffb_driver.set_hardware_autocenter(
                                impact_torque
                            )

                            crash_ffb_active = True

                            crash_ffb_strength = (
                                impact_torque
                            )

                            crash_ffb_until = (
                                now +
                                KICK_DURATION
                            )

                    # ------------------------------------------------
                    # Store sample
                    # ------------------------------------------------

                    previous_timestamp = timestamp

                    previous_accel = accel

            # =================================================
            # LAYER 1: SENSORS
            # =================================================

            state.update_imu(
                imu_hw.accel_x,
                imu_hw.accel_y,
                imu_hw.accel_z
            )

            haptic_fx = (
                haptics.calculate_effects()
            )

            # =================================================
            # LAYER 2: INPUTS
            # =================================================

            pygame.event.pump()

            steer_sens_pct, throttle_lim_pct = (
                get_settings()
            )

            steering_gain = (
                steer_sens_pct /
                100.0
            )

            throttle_limit = (
                throttle_lim_pct /
                100.0
            )

            # -------------------------------------------------
            # Steering
            # -------------------------------------------------

            raw_steer_axis = (
                -js.get_axis(0)
                if js.get_numaxes() > 0
                else 0.0
            )

            # -------------------------------------------------
            # Throttle
            # -------------------------------------------------

            throttle_axis = (
                max(
                    0.0,
                    min(
                        1.0,
                        (
                            1.0 -
                            js.get_axis(2)
                        ) /
                        2.0
                    )
                )
                if js.get_numaxes() > 2
                else 0.0
            )

            # -------------------------------------------------
            # Brake
            # -------------------------------------------------

            brake_axis = (
                max(
                    0.0,
                    min(
                        1.0,
                        (
                            1.0 -
                            js.get_axis(1)
                        ) /
                        2.0
                    )
                )
                if js.get_numaxes() > 1
                else 0.0
            )

            # =================================================
            # UPDATE VEHICLE STATE
            # =================================================

            state.update_inputs(
                raw_steer_axis *
                1000 *
                steering_gain,

                throttle_axis,

                brake_axis
            )

            # =================================================
            # CONTROL VALUES
            # =================================================

            steering = max(
                -1000,
                min(
                    1000,
                    int(
                        raw_steer_axis *
                        1000 *
                        steering_gain
                    )
                )
            )

            throttle = int(
                (
                    throttle_axis -
                    brake_axis
                ) *
                1000 *
                throttle_limit
            )

            if abs(throttle) < 20:

                throttle = 0

            # =================================================
            # EXISTING HAPTIC SYSTEM
            # =================================================

            kick_boost = (
                effects.compute_impact_kick(
                    state
                )
            )

            final_stiffness_pct = max(
                15.0,
                min(
                    100.0,
                    kick_boost +
                    haptic_fx.get(
                        "haptic_boost",
                        0.0
                    ) +
                    20.0
                )
            )

            if kick_boost > 0.0:

                print(
                    f"💥 FLICK IMPACT | "
                    f"Stiffness Boost: "
                    f"{final_stiffness_pct:.1f}%",
                    flush=True
                )

            # =================================================
            # FFB OUTPUT
            # =================================================

            # Crash FFB temporarily has priority.
            #
            # After KICK_DURATION, normal project FFB
            # automatically resumes.

            if crash_ffb_active:

                ffb_driver.set_hardware_autocenter(
                    crash_ffb_strength
                )

            else:

                ffb_driver.set_hardware_autocenter(
                    final_stiffness_pct
                )

            # =================================================
            # SEND CONTROL PACKET
            # =================================================

            packet = struct.pack(
                "<Bhh",
                PACKET_CONTROL,
                steering,
                throttle
            )

            ser.reset_output_buffer()

            ser.write(packet)

            # =================================================
            # LOOP TIMING
            # =================================================

            time.sleep(0.01)

    finally:

        # -----------------------------------------------------
        # Release FFB
        # -----------------------------------------------------

        try:

            ffb_driver.set_hardware_autocenter(
                0.0
            )

        except Exception:
            pass

        try:

            ffb_driver.stop()

        except Exception:
            pass

        # -----------------------------------------------------
        # Stop IMU
        # -----------------------------------------------------

        try:

            imu_hw.stop()

        except Exception:
            pass

        # -----------------------------------------------------
        # Close serial
        # -----------------------------------------------------

        try:

            ser.close()

        except Exception:
            pass

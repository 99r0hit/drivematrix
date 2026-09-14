#!/usr/bin/env python3

import time
import select
import evdev

import app


COCKPIT_ID = 1
VEHICLE_NAME = "Car 1"

CONTROL_INTERVAL = 0.02  # 50 Hz
CONTROL_SCALE = 1000
DEADZONE = 20


def clamp(value, low, high):
    return max(low, min(high, value))


def normalize_abs(value, absinfo):
    """Normalize a raw EV_ABS value using the device's real min/max."""
    minimum = float(absinfo.min)
    maximum = float(absinfo.max)

    if maximum <= minimum:
        return 0.0

    normalized = (
        (float(value) - minimum)
        / (maximum - minimum)
    )

    return clamp(normalized, 0.0, 1.0)


def steering_from_raw(value, absinfo):
    # G29 ABS_X:
    # left = -1.0, center ~= 0.0, right = +1.0
    normalized = normalize_abs(value, absinfo)
    return (normalized * 2.0) - 1.0


def pedal_from_raw(value, absinfo):
    # G29 pedal axes:
    # released = 1.0, fully pressed = 0.0
    normalized = normalize_abs(value, absinfo)
    return 1.0 - normalized


def get_settings():
    with app.cockpit_settings_lock:
        return dict(app.cockpit_settings[COCKPIT_ID])


def get_cockpit():
    return app.cockpit_manager.get_cockpit(COCKPIT_ID)


def send_zero(radio_id):
    app.cockpit_manager.radio_manager.send_control(
        radio_id,
        0,
        0
    )


def main():
    print()
    print("==============================")
    print("DriveMatrix Cockpit 1 Control Test")
    print("==============================")
    print()
    print("SAFETY:")
    print("  Keep the car wheels OFF THE GROUND for this test.")
    print("  Do not run app.py at the same time.")
    print()

    # Use the same discovery/pairing path as the current application.
    app.initialize_cockpit_system()

    cockpit = get_cockpit()

    if cockpit is None:
        raise RuntimeError("Cockpit 1 does not exist.")

    if cockpit.wheel is None:
        raise RuntimeError("Cockpit 1 has no wheel assigned.")

    if cockpit.radio_id is None:
        raise RuntimeError("Cockpit 1 has no radio assigned.")

    radio_id = cockpit.radio_id
    wheel_path = cockpit.wheel.path

    print(f"Cockpit 1 wheel : {cockpit.wheel.name}")
    print(f"Wheel device    : {wheel_path}")
    print(f"Radio ID        : {radio_id}")

    controller = app.cockpit_manager.radio_manager.get_radio(radio_id)
    if controller is None or not controller.connected:
        raise RuntimeError(
            f"Radio {radio_id} is not connected."
        )

    print(f"Radio port      : {controller.port}")
    print()

    # Vehicle selection is intentionally explicit for this first RF test.
    if cockpit.vehicle_name is not None:
        if cockpit.vehicle_name != VEHICLE_NAME:
            raise RuntimeError(
                f"Cockpit 1 already has vehicle {cockpit.vehicle_name}. "
                f"Clear it before this test."
            )
        print(f"Vehicle         : {cockpit.vehicle_name}")
    else:
        print(f"Selecting vehicle: {VEHICLE_NAME}")
        if not app.cockpit_manager.select_vehicle(
            COCKPIT_ID,
            VEHICLE_NAME
        ):
            raise RuntimeError(
                f"Could not select {VEHICLE_NAME}."
            )
        print(f"Vehicle         : {VEHICLE_NAME}")

    settings = get_settings()

    print()
    print("Settings")
    print(f"  Session duration : {settings['session_duration_minutes']} min")
    print(f"  Steering         : {settings['steering_sensitivity']}%")
    print(f"  Throttle         : {settings['throttle_sensitivity']}%")
    print()

    # Start the existing Pi-side session timer state.
    success, error = app.start_cockpit_session(COCKPIT_ID)
    if not success:
        raise RuntimeError(f"Could not start session: {error}")

    print("Session           : ACTIVE")
    print()
    print("CONTROL ACTIVE")
    print("Move the wheel/pedals.")
    print("Press Ctrl+C to stop.")
    print()

    device = evdev.InputDevice(wheel_path)

    abs_x_info = device.absinfo(evdev.ecodes.ABS_X)
    abs_y_info = device.absinfo(evdev.ecodes.ABS_Y)
    abs_z_info = device.absinfo(evdev.ecodes.ABS_Z)

    print("Axis ranges")
    print(
        f"  ABS_X: {abs_x_info.min} .. {abs_x_info.max}"
    )
    print(
        f"  ABS_Y: {abs_y_info.min} .. {abs_y_info.max}"
    )
    print(
        f"  ABS_Z: {abs_z_info.min} .. {abs_z_info.max}"
    )
    print()

    steering_axis = 0.0
    throttle_axis = 0.0
    brake_axis = 0.0

    last_send = 0.0
    last_print = 0.0

    try:
        while True:
            now = time.monotonic()

            # Drain available wheel events without blocking the control loop.
            readable, _, _ = select.select([device.fd], [], [], 0)

            if readable:
                for event in device.read():
                    if event.type != evdev.ecodes.EV_ABS:
                        continue

                    if event.code == evdev.ecodes.ABS_X:
                        steering_axis = steering_from_raw(
                            event.value,
                            abs_x_info
                        )

                    elif event.code == evdev.ecodes.ABS_Z:
                        throttle_axis = pedal_from_raw(
                            event.value,
                            abs_z_info
                        )

                    elif event.code == evdev.ecodes.ABS_Y:
                        brake_axis = pedal_from_raw(
                            event.value,
                            abs_y_info
                        )

            session = app.get_cockpit_session_state(COCKPIT_ID)

            if not session["active"]:
                send_zero(radio_id)
                print()
                print("SESSION INACTIVE -> CONTROL STOPPED")
                break

            if now - last_send < CONTROL_INTERVAL:
                time.sleep(0.001)
                continue

            settings = get_settings()

            steering_sensitivity = (
                settings["steering_sensitivity"] / 100.0
            )
            throttle_sensitivity = (
                settings["throttle_sensitivity"] / 100.0
            )

            steering = int(
                clamp(
                    steering_axis * CONTROL_SCALE
                    * steering_sensitivity,
                    -CONTROL_SCALE,
                    CONTROL_SCALE
                )
            )

            throttle = (
                throttle_axis
                - brake_axis
            )

            throttle = int(
                clamp(
                    throttle * CONTROL_SCALE
                    * throttle_sensitivity,
                    -CONTROL_SCALE,
                    CONTROL_SCALE
                )
            )

            if abs(throttle) < DEADZONE:
                throttle = 0

            # Safety gate: no active vehicle/session means zero command.
            if not session["active"] or cockpit.vehicle_name != VEHICLE_NAME:
                steering = 0
                throttle = 0

            ok = app.cockpit_manager.radio_manager.send_control(
                radio_id,
                steering,
                throttle
            )

            if not ok:
                print("CONTROL SEND FAILED")
                send_zero(radio_id)
                break

            last_send = now

            if now - last_print >= 0.25:
                remaining = session["remaining_seconds"]
                print(
                    f"Steering={steering:+5d}  "
                    f"Throttle={throttle:+5d}  "
                    f"Remaining={remaining:03d}s"
                )
                last_print = now

            time.sleep(0.001)

    except KeyboardInterrupt:
        print()
        print("Ctrl+C received.")

    finally:
        # Always command zero before ending the test.
        try:
            send_zero(radio_id)
        except Exception:
            pass

        # Release the vehicle and stop the Pi-side session.
        try:
            app.stop_cockpit_session(COCKPIT_ID)
        except Exception:
            pass

        try:
            device.close()
        except Exception:
            pass

        try:
            app.cockpit_manager.close()
        except Exception:
            pass

        print("Final control command: 0,0")
        print("Session stopped.")
        print("Radio disconnected by test cleanup.")


if __name__ == "__main__":
    main()

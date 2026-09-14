import time
import select
import evdev
import glob
import minimal_drive
from g29_ffb import G29FFB
import threading
import subprocess
from flask import Flask, render_template, request, jsonify
from wifi_manager import monitor_wifi, scan_networks, connect_wifi
from cockpit_manager import CockpitManager
from radio_discovery import discover_radios
from telemetry_receiver import TelemetryReceiver


app = Flask(__name__)

session_active = True
autocenter_enabled = True

# Start background Wi-Fi connection monitor
threading.Thread(target=monitor_wifi, daemon=True).start()

# Legacy/global vehicle control settings retained for the existing drive loop.
# Per-cockpit settings are stored separately below and are exposed through
# the cockpit API. The actual multi-cockpit drive loop will use these later
# when wheel->radio control is integrated.
steering_sensitivity = 100
throttle_limit = 100

# Live Haptic Engine Tuning Parameters
haptic_settings = {
    "grip_threshold": 0.65,
    "bump_sensitivity": 2.0,
    "centering_gain": 25.0,
    "alpha": 0.20
}

start_time = time.time()
# Per-cockpit G29 FFB instances. Each instance is bound to the same
# wheel device owned by that cockpit's control worker.
cockpit_ffb_instances = {}
cockpit_ffb_lock = threading.Lock()

# Step 1 telemetry receiver. It only receives telemetry and reports impacts;
# FFB actuation will be connected after telemetry reception is validated.

# Legacy/global reference retained only for compatibility with the existing
# single-cockpit API. It is never bound to a hardcoded /dev/input/eventX.
g29_ffb_instance = None

# Logical cockpit / vehicle assignment manager.
cockpit_manager = CockpitManager()
cockpit_system_initialized = False
cockpit_init_lock = threading.Lock()

# Per-cockpit session/settings state.
#
# Session duration is configured by the user. The timer itself is owned by
# the Raspberry Pi, not by the browser, so a page refresh/disconnect cannot
# extend or cancel a running session.
cockpit_settings_lock = threading.Lock()
cockpit_settings = {
    cockpit_id: {
        "session_duration_minutes": 10,
        "steering_sensitivity": 100,
        "throttle_sensitivity": 100,
    }
    for cockpit_id in range(1, cockpit_manager.max_cockpits + 1)
}

cockpit_sessions_lock = threading.Lock()
cockpit_sessions = {
    cockpit_id: {
        "active": False,
        "started_at": None,
        "ends_at": None,
    }
    for cockpit_id in range(1, cockpit_manager.max_cockpits + 1)
}


# FFB impact configuration. These values are intentionally identical to the
# standalone, already-validated ffb_test.py implementation.
FFB_MIN = 30.0
FFB_MAX = 100.0
FFB_MULTIPLIER = 50.0
FFB_KICK_DURATION = 0.25


def _active_ffb_cockpit_ids():
    active = []
    with cockpit_sessions_lock:
        for cockpit_id, session in cockpit_sessions.items():
            if session["active"]:
                active.append(cockpit_id)
    return active


def _release_cockpit_impact_ffb(cockpit_id):
    try:
        set_autocenter_hardware(0.0, cockpit_id=cockpit_id)
        print(f"[FFB] Cockpit {cockpit_id} impact kick released")
    except Exception as exc:
        print(
            f"[FFB] Cockpit {cockpit_id} impact release error: {exc}"
        )


def handle_telemetry_impact(impact):
    """Route a validated telemetry impact to the active cockpit G29.

    The current 29-byte telemetry packet contains the ESP32 source IP but no
    RX ID. Therefore an impact is routed automatically only when exactly one
    cockpit session is active. This is safe for the current one-cockpit test
    and avoids ever sending one vehicle's impact to an arbitrary cockpit.
    Multi-cockpit telemetry identity will be added only when the existing
    protocol provides a receiver identity.
    """
    active_cockpits = _active_ffb_cockpit_ids()

    if len(active_cockpits) != 1:
        print(
            f"[FFB] Impact not routed | source={impact['source_ip']} | "
            f"active_cockpits={active_cockpits}"
        )
        return

    cockpit_id = active_cockpits[0]
    ffb = get_ffb_device(cockpit_id)

    if ffb is None:
        print(
            f"[FFB] Impact not routed | cockpit {cockpit_id} has no FFB device"
        )
        return

    jerk = float(impact["jerk"])
    severity = jerk / 10000.0
    strength = min(FFB_MAX, max(FFB_MIN, severity * FFB_MULTIPLIER))

    print(
        f"[FFB] IMPACT → Cockpit {cockpit_id} | "
        f"source={impact['source_ip']} | jerk={jerk:.1f} | "
        f"strength={strength:.1f}%"
    )

    try:
        ffb.set_hardware_autocenter(strength)
    except Exception as exc:
        print(f"[FFB] Cockpit {cockpit_id} kick error: {exc}")
        return

    timer = threading.Timer(
        FFB_KICK_DURATION,
        _release_cockpit_impact_ffb,
        args=(cockpit_id,)
    )
    timer.daemon = True
    timer.start()


telemetry_receiver = TelemetryReceiver(
    on_telemetry=lambda event: None,
    on_impact=handle_telemetry_impact,
)


# Multi-cockpit wheel -> RadioManager control.
# One generic worker implementation is used for every logical cockpit.
# Only cockpits with successfully discovered wheel/radio pairs will actively
# send control commands; unused cockpits remain idle.
COCKPIT_CONTROL_INTERVAL = 0.02  # 50 Hz
COCKPIT_CONTROL_SCALE = 1000
COCKPIT_CONTROL_DEADZONE = 20
cockpit_control_stop = threading.Event()
cockpit_control_threads = {}

# Runtime wheel hot-plug monitor. This watches for G29 connection changes
# while Flask remains running; Nano/radio connections are not rediscovered.
COCKPIT_DEVICE_SCAN_INTERVAL = 1.0
cockpit_device_monitor_stop = threading.Event()
cockpit_device_monitor_thread = None
cockpit_wheel_state_lock = threading.Lock()


def get_ffb_device(cockpit_id=None):
    """Return the FFB instance bound to a logical cockpit's G29."""
    global g29_ffb_instance

    with cockpit_ffb_lock:
        if cockpit_id is not None:
            return cockpit_ffb_instances.get(cockpit_id)

        # Compatibility path for the existing global API: return the first
        # currently bound cockpit FFB instance, if any.
        for ffb in cockpit_ffb_instances.values():
            if ffb is not None:
                g29_ffb_instance = ffb
                return ffb
        return g29_ffb_instance


def _set_cockpit_ffb_instance(cockpit_id, ffb):
    global g29_ffb_instance
    with cockpit_ffb_lock:
        old_ffb = cockpit_ffb_instances.get(cockpit_id)
        cockpit_ffb_instances[cockpit_id] = ffb
        if g29_ffb_instance is None:
            g29_ffb_instance = ffb
    if old_ffb is not None and old_ffb is not ffb:
        try:
            old_ffb.stop()
        except Exception:
            pass


def _remove_cockpit_ffb_instance(cockpit_id, expected=None):
    global g29_ffb_instance
    with cockpit_ffb_lock:
        current = cockpit_ffb_instances.get(cockpit_id)
        if expected is not None and current is not expected:
            return current
        cockpit_ffb_instances.pop(cockpit_id, None)
        if g29_ffb_instance is current:
            g29_ffb_instance = next(
                (ffb for ffb in cockpit_ffb_instances.values() if ffb is not None),
                None,
            )
        return current


def set_autocenter_hardware(enabled: bool, cockpit_id=None):
    """Set hardware centering for one cockpit, or all active cockpit wheels."""
    strength = 22000 if enabled else 0
    if cockpit_id is not None:
        ffb = get_ffb_device(cockpit_id)
        if ffb:
            ffb.set_autocenter(strength)
        return
    with cockpit_ffb_lock:
        instances = list(cockpit_ffb_instances.values())
    for ffb in instances:
        if ffb:
            try:
                ffb.set_autocenter(strength)
            except Exception as exc:
                print(f"[FFB Error] Auto-center update failed: {exc}")


def drive_worker():
    # Preserve the existing single-drive-loop behavior for now.
    # Per-cockpit control becomes active at the wheel->Nano multi-cockpit stage.
    set_autocenter_hardware(autocenter_enabled)
    minimal_drive.run_loop(
        is_active_callback=lambda: session_active,
        get_settings=lambda: (steering_sensitivity, throttle_limit),
        is_autocenter_enabled=lambda: autocenter_enabled,
        get_haptic_settings=lambda: haptic_settings
    )


@app.route('/')
def home():
    return render_template('index.html')


def initialize_cockpit_system():
    """Initial discovery of wheels, Nanos and RXs.

    Wheels and Nanos are discovered independently. A cockpit can therefore
    contain a wheel without a Nano, while extra Nanos remain available.
    Existing Nano controllers are preserved by RadioManager.refresh discovery.
    """
    global cockpit_system_initialized

    with cockpit_init_lock:
        if cockpit_system_initialized:
            return

        refresh_cockpit_devices()
        cockpit_system_initialized = True


def _wheel_identity(wheel):
    """Return the best available runtime identity for a discovered G29."""
    uniq = str(getattr(wheel, "uniq", "") or "").strip()
    phys = str(getattr(wheel, "phys", "") or "").strip()
    if uniq:
        return ("uniq", uniq)
    if phys:
        return ("phys", phys)
    return ("path", str(getattr(wheel, "path", "")))


def _current_wheel_identities():
    identities = {}
    for cockpit_id in range(1, cockpit_manager.max_cockpits + 1):
        cockpit = cockpit_manager.get_cockpit(cockpit_id)
        if cockpit is not None and cockpit.wheel is not None:
            identities[cockpit_id] = _wheel_identity(cockpit.wheel)
    return identities


def _current_radio_ids():
    return {
        cockpit_id: cockpit_manager.get_cockpit(cockpit_id).radio_id
        for cockpit_id in range(1, cockpit_manager.max_cockpits + 1)
        if cockpit_manager.get_cockpit(cockpit_id) is not None
        and cockpit_manager.get_cockpit(cockpit_id).radio_id is not None
    }


def _stop_session_for_device_loss(cockpit_id, reason):
    if cockpit_sessions[cockpit_id]["active"]:
        stop_cockpit_session(cockpit_id)
    cockpit = cockpit_manager.get_cockpit(cockpit_id)
    if cockpit is not None and cockpit.vehicle_name is not None:
        # The radio may already have disappeared, so clear the logical state
        # directly if normal RadioManager cleanup is no longer possible.
        cockpit.vehicle_name = None
    print(f"[Hotplug] Cockpit {cockpit_id}: {reason}")


def reconcile_cockpit_devices():
    """Rediscover wheels + Nanos + RXs and reconcile logical cockpits."""
    with cockpit_wheel_state_lock:
        old_wheel_ids = _current_wheel_identities()
        old_radio_ids = _current_radio_ids()

        stop_cockpit_control_workers()

        try:
            # WheelManager discovery closes its previous discovery objects.
            wheels = cockpit_manager.wheel_manager.discover()

            # RadioManager discovery preserves connected controllers, adds new
            # Nanos, and removes only Nanos that are physically gone.
            radios = cockpit_manager.radio_manager.discover()
            cockpit_manager.connect_radios()

            discovered_wheels = {
                _wheel_identity(wheel): wheel for wheel in wheels
            }
            discovered_radio_ids = set(radios.keys())

            # Preserve still-connected wheels and radios in their current
            # logical cockpits.
            used_wheels = set()
            used_radios = set()

            for cockpit_id in range(1, cockpit_manager.max_cockpits + 1):
                cockpit = cockpit_manager.get_cockpit(cockpit_id)
                if cockpit is None:
                    continue

                old_wheel = old_wheel_ids.get(cockpit_id)
                if old_wheel is not None:
                    replacement = discovered_wheels.get(old_wheel)
                    if replacement is not None:
                        cockpit.wheel = replacement
                        used_wheels.add(old_wheel)
                    else:
                        if cockpit.wheel is not None:
                            _stop_session_for_device_loss(
                                cockpit_id, "wheel disconnected"
                            )
                        cockpit.wheel = None

                old_radio = old_radio_ids.get(cockpit_id)
                if old_radio is not None:
                    if old_radio in discovered_radio_ids:
                        cockpit.radio_id = old_radio
                        used_radios.add(old_radio)
                    else:
                        _stop_session_for_device_loss(
                            cockpit_id, "Nano disconnected"
                        )
                        cockpit.radio_id = None

            # Newly discovered wheels fill the lowest cockpit slots first.
            new_wheels = [
                wheel for identity, wheel in discovered_wheels.items()
                if identity not in used_wheels
            ]

            for cockpit_id in range(1, cockpit_manager.max_cockpits + 1):
                if not new_wheels:
                    break
                cockpit = cockpit_manager.get_cockpit(cockpit_id)
                if cockpit is None or cockpit.wheel is not None:
                    continue

                wheel = new_wheels.pop(0)
                index = wheels.index(wheel)
                try:
                    cockpit_manager.assign_wheel(cockpit_id, index)
                    print(
                        f"[Hotplug] Cockpit {cockpit_id} wheel connected: "
                        f"{wheel.path}"
                    )
                except Exception as exc:
                    print(
                        f"[Hotplug] Wheel assignment failed for Cockpit "
                        f"{cockpit_id}: {exc}"
                    )

            # Newly discovered Nanos are assigned only to cockpits that have
            # wheels. Extra Nanos remain connected and available.
            new_radios = [
                radio_id for radio_id in radios.keys()
                if radio_id not in used_radios
            ]

            for cockpit_id in range(1, cockpit_manager.max_cockpits + 1):
                if not new_radios:
                    break
                cockpit = cockpit_manager.get_cockpit(cockpit_id)
                if cockpit is None:
                    continue
                if cockpit.wheel is None or cockpit.radio_id is not None:
                    continue

                radio_id = new_radios.pop(0)
                try:
                    cockpit_manager.assign_radio(cockpit_id, radio_id)
                    print(
                        f"[Hotplug] Cockpit {cockpit_id} Nano connected: "
                        f"{radio_id}"
                    )
                except Exception as exc:
                    print(
                        f"[Hotplug] Nano assignment failed for Cockpit "
                        f"{cockpit_id}: {exc}"
                    )

            # Refresh RX visibility through every connected Nano. This does
            # not alter permanent RX -> vehicle registry mappings.
            cockpit_manager.radio_manager.discover_receivers()

            # Restart control workers only where both wheel and connected Nano
            # are present. A wheel without a Nano remains visible but cannot
            # control a vehicle.
            for cockpit_id in range(1, cockpit_manager.max_cockpits + 1):
                cockpit = cockpit_manager.get_cockpit(cockpit_id)
                if (
                    cockpit is not None
                    and cockpit.wheel is not None
                    and cockpit.radio_id is not None
                ):
                    start_cockpit_control_worker(cockpit_id)

        except Exception as exc:
            print(f"[Hotplug] Device reconciliation error: {exc}")
            # Never discard the logical state because a transient scan failed.
            for cockpit_id in range(1, cockpit_manager.max_cockpits + 1):
                cockpit = cockpit_manager.get_cockpit(cockpit_id)
                if (
                    cockpit is not None
                    and cockpit.wheel is not None
                    and cockpit.radio_id is not None
                ):
                    start_cockpit_control_worker(cockpit_id)


def refresh_cockpit_devices():
    """Full manual discovery used by startup and the Refresh button."""
    print("[Discovery] Refreshing wheels, Nanos and RXs...")
    reconcile_cockpit_devices()


def _scan_g29_identities():
    """Scan G29 identities without closing WheelManager control handles."""
    identities = set()
    g29_name = "Logitech G29 Driving Force Racing Wheel"

    for path in sorted(glob.glob("/dev/input/event*")):
        device = None
        try:
            device = evdev.InputDevice(path)
            if device.name != g29_name:
                continue
            identities.add(_wheel_identity(device))
        except Exception:
            pass
        finally:
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass

    return identities


def _scan_radio_ids():
    """Probe Nano identities without replacing live RadioController objects."""
    ids = set()
    try:
        for radio in discover_radios():
            ids.add(str(radio.radio_id).strip())
    except Exception as exc:
        print(f"[Hotplug] Nano probe error: {exc}")
    return ids


def cockpit_device_monitor_worker():
    """Detect G29/Nano plug or unplug changes without disturbing live handles."""
    previous_wheels = _scan_g29_identities()
    previous_radios = _scan_radio_ids()

    while not cockpit_device_monitor_stop.wait(COCKPIT_DEVICE_SCAN_INTERVAL):
        try:
            current_wheels = _scan_g29_identities()
            current_radios = _scan_radio_ids()

            if current_wheels != previous_wheels or current_radios != previous_radios:
                print(
                    f"[Hotplug] Device change detected: "
                    f"wheels {len(previous_wheels)} -> {len(current_wheels)}, "
                    f"Nanos {len(previous_radios)} -> {len(current_radios)}"
                )
                reconcile_cockpit_devices()
                previous_wheels = _scan_g29_identities()
                previous_radios = _scan_radio_ids()

        except Exception as exc:
            print(f"[Hotplug] Monitor error: {exc}")


def start_cockpit_device_monitor():
    global cockpit_device_monitor_thread
    if (
        cockpit_device_monitor_thread is not None
        and cockpit_device_monitor_thread.is_alive()
    ):
        return
    cockpit_device_monitor_stop.clear()
    cockpit_device_monitor_thread = threading.Thread(
        target=cockpit_device_monitor_worker,
        name="cockpit-device-monitor",
        daemon=True
    )
    cockpit_device_monitor_thread.start()
    print("[Hotplug] Wheel/Nano auto-detection monitor started")


def stop_cockpit_device_monitor():
    cockpit_device_monitor_stop.set()
    if cockpit_device_monitor_thread is not None:
        cockpit_device_monitor_thread.join(timeout=2.0)


def _clamp(value, low, high):
    return max(low, min(high, value))


def _steering_from_event(event_value, device):
    """Normalize G29 ABS_X to approximately -1..+1."""
    info = device.absinfo(evdev.ecodes.ABS_X)
    minimum = float(info.min)
    maximum = float(info.max)

    if maximum <= minimum:
        return 0.0

    normalized = (
        ((float(event_value) - minimum) / (maximum - minimum)) * 2.0
    ) - 1.0
    return _clamp(normalized, -1.0, 1.0)


def _pedal_from_event(event_value, device, code):
    """Normalize a G29 pedal axis to 0..1 pressed."""
    info = device.absinfo(code)
    minimum = float(info.min)
    maximum = float(info.max)

    if maximum <= minimum:
        return 0.0

    released_to_pressed = (
        (float(event_value) - minimum) / (maximum - minimum)
    )

    # G29 pedal axes observed in the standalone test use released ~= 1
    # and pressed ~= 0.
    return _clamp(1.0 - released_to_pressed, 0.0, 1.0)


def _send_cockpit_zero(radio_id):
    try:
        cockpit_manager.radio_manager.send_control(
            radio_id,
            0,
            0
        )
    except Exception as exc:
        print(
            f"[Control] Zero command failed for radio "
            f"{radio_id}: {exc}"
        )


def cockpit_control_worker(cockpit_id):
    """
    Per-cockpit control path:
        G29 -> per-cockpit sensitivity -> RadioManager -> selected vehicle

    Each cockpit has its own wheel device and RadioManager radio. This worker
    owns only the wheel input device. RadioManager remains the owner of the
    Nano serial connection.
    """
    device = None
    ffb = None
    radio_id = None

    print(f"[Control] Cockpit {cockpit_id} control worker starting")

    try:
        initialize_cockpit_system()

        cockpit = cockpit_manager.get_cockpit(cockpit_id)
        if cockpit is None:
            print(f"[Control] Cockpit {cockpit_id} does not exist")
            return

        if cockpit.wheel is None:
            print(f"[Control] Cockpit {cockpit_id} has no wheel assigned")
            return

        if cockpit.radio_id is None:
            print(f"[Control] Cockpit {cockpit_id} has no radio assigned")
            return

        radio_id = cockpit.radio_id
        device = evdev.InputDevice(cockpit.wheel.path)

        # Bind FFB to this cockpit's exact G29 device. Do not scan for the
        # first EV_FF device and do not use a hardcoded event node.
        ffb = G29FFB(device)
        _set_cockpit_ffb_instance(cockpit_id, ffb)
        ffb.disable_autocenter()

        print(
            f"[Control] Cockpit {cockpit_id}: "
            f"wheel={cockpit.wheel.name}, "
            f"device={cockpit.wheel.path}, "
            f"FFB=READY, "
            f"radio={radio_id}"
        )

        steering_axis = 0.0
        throttle_axis = 0.0
        brake_axis = 0.0
        last_send = 0.0

        while not cockpit_control_stop.is_set():
            now = time.monotonic()

            # Drain all currently available wheel events without blocking.
            readable, _, _ = select.select([device.fd], [], [], 0)

            if readable:
                for event in device.read():
                    if event.type != evdev.ecodes.EV_ABS:
                        continue

                    if event.code == evdev.ecodes.ABS_X:
                        steering_axis = _steering_from_event(
                            event.value,
                            device
                        )

                    elif event.code == evdev.ecodes.ABS_Z:
                        throttle_axis = _pedal_from_event(
                            event.value,
                            device,
                            evdev.ecodes.ABS_Z
                        )

                    elif event.code == evdev.ecodes.ABS_Y:
                        brake_axis = _pedal_from_event(
                            event.value,
                            device,
                            evdev.ecodes.ABS_Y
                        )

            if now - last_send < COCKPIT_CONTROL_INTERVAL:
                time.sleep(0.001)
                continue

            session = get_cockpit_session_state(cockpit_id)
            cockpit = cockpit_manager.get_cockpit(cockpit_id)

            # Safety gate: no active session or no selected vehicle means
            # command zero. The Nano stays connected.
            if (
                not session["active"]
                or cockpit is None
                or cockpit.vehicle_name is None
            ):
                steering = 0
                throttle = 0
            else:
                with cockpit_settings_lock:
                    settings = dict(cockpit_settings[cockpit_id])

                steering_sensitivity = (
                    settings["steering_sensitivity"] / 100.0
                )
                throttle_sensitivity = (
                    settings["throttle_sensitivity"] / 100.0
                )

                steering = int(
                    _clamp(
                        steering_axis
                        * COCKPIT_CONTROL_SCALE
                        * steering_sensitivity,
                        -COCKPIT_CONTROL_SCALE,
                        COCKPIT_CONTROL_SCALE
                    )
                )

                throttle = int(
                    _clamp(
                        (throttle_axis - brake_axis)
                        * COCKPIT_CONTROL_SCALE
                        * throttle_sensitivity,
                        -COCKPIT_CONTROL_SCALE,
                        COCKPIT_CONTROL_SCALE
                    )
                )

                if abs(throttle) < COCKPIT_CONTROL_DEADZONE:
                    throttle = 0

            ok = cockpit_manager.radio_manager.send_control(
                radio_id,
                steering,
                throttle
            )

            if not ok:
                print(
                    f"[Control] Cockpit {cockpit_id} control send failed "
                    f"for radio {radio_id}"
                )
                _send_cockpit_zero(radio_id)
                time.sleep(0.1)
            else:
                last_send = now

    except Exception as exc:
        print(f"[Control] Cockpit {cockpit_id} worker error: {exc}")
        if radio_id is not None:
            _send_cockpit_zero(radio_id)

    finally:
        if radio_id is not None:
            _send_cockpit_zero(radio_id)

        if ffb is not None:
            try:
                ffb.stop()
            except Exception:
                pass
            _remove_cockpit_ffb_instance(cockpit_id, expected=ffb)

        if device is not None:
            try:
                device.close()
            except Exception:
                pass

        print(f"[Control] Cockpit {cockpit_id} control worker stopped")


def start_cockpit_control_worker(cockpit_id):
    if cockpit_id in cockpit_control_threads:
        thread = cockpit_control_threads[cockpit_id]
        if thread.is_alive():
            return

    cockpit_control_stop.clear()
    thread = threading.Thread(
        target=cockpit_control_worker,
        args=(cockpit_id,),
        name=f"cockpit{cockpit_id}-control",
        daemon=True
    )
    cockpit_control_threads[cockpit_id] = thread
    thread.start()


def stop_cockpit_control_workers():
    cockpit_control_stop.set()

    for thread in cockpit_control_threads.values():
        if thread is not None:
            thread.join(timeout=2.0)

    cockpit_control_threads.clear()

    with cockpit_ffb_lock:
        remaining_ffb = list(cockpit_ffb_instances.items())
        cockpit_ffb_instances.clear()

    for cockpit_id, ffb in remaining_ffb:
        try:
            ffb.stop()
        except Exception:
            pass

    global g29_ffb_instance
    g29_ffb_instance = None


def get_cockpit_session_state(cockpit_id):
    """Return the current session state for one cockpit."""
    now = time.time()

    with cockpit_sessions_lock:
        session = cockpit_sessions[cockpit_id]

        if session["active"] and session["ends_at"] is not None:
            remaining = max(0, int(session["ends_at"] - now))
        else:
            remaining = 0

        return {
            "active": bool(session["active"]),
            "remaining_seconds": remaining,
        }


def expire_cockpit_session(cockpit_id):
    """Stop a cockpit session and release its vehicle assignment."""
    with cockpit_sessions_lock:
        session = cockpit_sessions[cockpit_id]

        if not session["active"]:
            return

        session["active"] = False
        session["started_at"] = None
        session["ends_at"] = None

    try:
        cockpit = cockpit_manager.get_cockpit(cockpit_id)

        if cockpit is not None and cockpit.vehicle_name is not None:
            success = cockpit_manager.clear_vehicle(cockpit_id)

            if success:
                print(
                    f"[Session] Cockpit {cockpit_id} expired; "
                    "vehicle released"
                )
            else:
                print(
                    f"[Session] Cockpit {cockpit_id} expired; "
                    "vehicle release failed"
                )
        else:
            print(
                f"[Session] Cockpit {cockpit_id} expired; "
                "no vehicle to release"
            )
    except Exception as exc:
        print(
            f"[Session] Cockpit {cockpit_id} expiry cleanup error: {exc}"
        )


def cockpit_session_worker():
    """Background Pi-owned timer for all logical cockpits."""
    while True:
        now = time.time()
        expired = []

        with cockpit_sessions_lock:
            for cockpit_id, session in cockpit_sessions.items():
                if (
                    session["active"]
                    and session["ends_at"] is not None
                    and now >= session["ends_at"]
                ):
                    expired.append(cockpit_id)

        for cockpit_id in expired:
            expire_cockpit_session(cockpit_id)

        time.sleep(0.25)


def start_cockpit_session(cockpit_id):
    """Start a timed session for one cockpit."""
    initialize_cockpit_system()

    cockpit = cockpit_manager.get_cockpit(cockpit_id)

    if cockpit is None:
        return False, "Invalid cockpit ID"

    if cockpit.wheel is None:
        return False, f"Cockpit {cockpit_id} has no wheel assigned"

    if cockpit.radio_id is None:
        return False, f"Cockpit {cockpit_id} has no radio assigned"

    if cockpit.vehicle_name is None:
        return False, f"Cockpit {cockpit_id} has no vehicle assigned"

    with cockpit_settings_lock:
        duration_minutes = cockpit_settings[cockpit_id][
            "session_duration_minutes"
        ]

    now = time.time()
    ends_at = now + (duration_minutes * 60)

    with cockpit_sessions_lock:
        if cockpit_sessions[cockpit_id]["active"]:
            return False, f"Cockpit {cockpit_id} session is already active"

        cockpit_sessions[cockpit_id] = {
            "active": True,
            "started_at": now,
            "ends_at": ends_at,
        }

    print(
        f"[Session] Cockpit {cockpit_id} started: "
        f"{duration_minutes} minute(s), vehicle={cockpit.vehicle_name}"
    )

    return True, None


def stop_cockpit_session(cockpit_id):
    """Stop a timed session and release its vehicle assignment."""
    initialize_cockpit_system()

    cockpit = cockpit_manager.get_cockpit(cockpit_id)

    if cockpit is None:
        return False, "Invalid cockpit ID"

    with cockpit_sessions_lock:
        was_active = cockpit_sessions[cockpit_id]["active"]

        cockpit_sessions[cockpit_id] = {
            "active": False,
            "started_at": None,
            "ends_at": None,
        }

    if cockpit.vehicle_name is not None:
        try:
            if not cockpit_manager.clear_vehicle(cockpit_id):
                return False, "Vehicle release failed"
        except Exception as exc:
            return False, str(exc)

    print(f"[Session] Cockpit {cockpit_id} stopped")

    if not was_active:
        return True, None

    return True, None


def get_cockpit_payload():
    """Return UI-safe logical cockpit, vehicle and per-cockpit settings."""
    initialize_cockpit_system()

    registry = cockpit_manager.radio_manager.vehicle_registry
    vehicles = []

    for receiver_id, vehicle in registry.vehicles.items():
        if not isinstance(vehicle, dict):
            continue

        name = str(vehicle.get("name", "")).strip()
        if not name:
            continue

        vehicles.append({
            "name": name,
            "receiver_id": str(receiver_id).strip().upper()
        })

    vehicles.sort(key=lambda item: item["name"])

    cockpits = []

    with cockpit_settings_lock:
        settings_snapshot = {
            cockpit_id: dict(settings)
            for cockpit_id, settings in cockpit_settings.items()
        }

    for cockpit_id in range(1, cockpit_manager.max_cockpits + 1):
        cockpit = cockpit_manager.get_cockpit(cockpit_id)

        wheel = None
        if cockpit.wheel is not None:
            wheel = {
                "name": cockpit.wheel.name,
                "path": cockpit.wheel.path,
                "phys": cockpit.wheel.phys
            }

        radio = None
        if cockpit.radio_id is not None:
            controller = cockpit_manager.radio_manager.get_radio(
                cockpit.radio_id
            )
            radio = {
                "radio_id": cockpit.radio_id,
                "port": controller.port if controller else None,
                "connected": bool(controller and controller.connected)
            }

        cockpits.append({
            "cockpit_id": cockpit_id,
            "wheel": wheel,
            "radio": radio,
            "vehicle": cockpit.vehicle_name,
            "settings": settings_snapshot[cockpit_id],
            "session": {
                **get_cockpit_session_state(cockpit_id),
                "duration_minutes": settings_snapshot[cockpit_id][
                    "session_duration_minutes"
                ],
            },
        })

    radios = []
    assigned_radio_ids = {
        cockpit.radio_id
        for cockpit in cockpit_manager.cockpits.values()
        if cockpit.radio_id is not None
    }
    for radio_id, controller in cockpit_manager.radio_manager.radios.items():
        radios.append({
            "radio_id": radio_id,
            "port": controller.port,
            "connected": bool(controller.connected),
            "assigned": radio_id in assigned_radio_ids
        })
    radios.sort(key=lambda item: item["radio_id"])

    return {
        "success": True,
        "cockpits": cockpits,
        "vehicles": vehicles,
        "radios": radios
    }


@app.route('/api/cockpits/refresh', methods=['POST'])
def cockpit_refresh():
    try:
        refresh_cockpit_devices()
        return jsonify(get_cockpit_payload())
    except Exception as e:
        print(f"[Cockpit API] Refresh error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/cockpits', methods=['GET'])
def cockpit_status():
    try:
        return jsonify(get_cockpit_payload())
    except Exception as e:
        print(f"[Cockpit API] Status error: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/cockpits/<int:cockpit_id>/vehicle', methods=['POST'])
def set_cockpit_vehicle(cockpit_id):
    data = request.get_json() or {}
    vehicle_name = data.get("vehicle_name")

    try:
        if vehicle_name in (None, "", "UNASSIGNED"):
            success = cockpit_manager.clear_vehicle(cockpit_id)
            selected_vehicle = None
        else:
            vehicle_name = str(vehicle_name).strip()
            success = cockpit_manager.select_vehicle(
                cockpit_id,
                vehicle_name
            )
            selected_vehicle = vehicle_name if success else None

        if not success:
            return jsonify({
                "success": False,
                "error": "Vehicle assignment failed"
            }), 400

        return jsonify({
            "success": True,
            "cockpit_id": cockpit_id,
            "vehicle": selected_vehicle
        })

    except Exception as e:
        print(f"[Cockpit API] Vehicle assignment error: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400


def validate_cockpit_id(cockpit_id):
    return 1 <= cockpit_id <= cockpit_manager.max_cockpits


@app.route('/api/cockpits/<int:cockpit_id>/settings', methods=['GET', 'POST'])
def cockpit_settings_api(cockpit_id):
    if not validate_cockpit_id(cockpit_id):
        return jsonify({
            "success": False,
            "error": "Invalid cockpit ID"
        }), 400

    if request.method == 'GET':
        with cockpit_settings_lock:
            return jsonify({
                "success": True,
                "cockpit_id": cockpit_id,
                "settings": dict(cockpit_settings[cockpit_id])
            })

    data = request.get_json() or {}

    duration_value = data.get("session_duration_minutes")
    sensitivity_value = data.get("steering_sensitivity")
    throttle_value = data.get("throttle_sensitivity")

    with cockpit_sessions_lock:
        session_active_for_cockpit = cockpit_sessions[cockpit_id]["active"]

    if duration_value is not None:
        if session_active_for_cockpit:
            return jsonify({
                "success": False,
                "error": "Cannot change session duration while session is active"
            }), 400

        try:
            duration_value = int(duration_value)
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "Session duration must be an integer"
            }), 400

        if not 1 <= duration_value <= 120:
            return jsonify({
                "success": False,
                "error": "Session duration must be between 1 and 120 minutes"
            }), 400

    if sensitivity_value is not None:
        try:
            sensitivity_value = int(sensitivity_value)
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "Steering sensitivity must be an integer"
            }), 400

        if not 10 <= sensitivity_value <= 200:
            return jsonify({
                "success": False,
                "error": "Steering sensitivity must be between 10 and 200"
            }), 400

    if throttle_value is not None:
        try:
            throttle_value = int(throttle_value)
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "Throttle sensitivity must be an integer"
            }), 400

        if not 10 <= throttle_value <= 100:
            return jsonify({
                "success": False,
                "error": "Throttle sensitivity must be between 10 and 100"
            }), 400

    with cockpit_settings_lock:
        current = cockpit_settings[cockpit_id]

        if duration_value is not None:
            current["session_duration_minutes"] = duration_value

        if sensitivity_value is not None:
            current["steering_sensitivity"] = sensitivity_value

        if throttle_value is not None:
            current["throttle_sensitivity"] = throttle_value

        saved = dict(current)

    return jsonify({
        "success": True,
        "cockpit_id": cockpit_id,
        "settings": saved
    })


@app.route('/api/cockpits/<int:cockpit_id>/session/start', methods=['POST'])
def cockpit_session_start_api(cockpit_id):
    if not validate_cockpit_id(cockpit_id):
        return jsonify({
            "success": False,
            "error": "Invalid cockpit ID"
        }), 400

    success, error = start_cockpit_session(cockpit_id)

    if not success:
        return jsonify({
            "success": False,
            "error": error
        }), 400

    return jsonify({
        "success": True,
        "cockpit_id": cockpit_id,
        "session": get_cockpit_session_state(cockpit_id)
    })


@app.route('/api/cockpits/<int:cockpit_id>/session/stop', methods=['POST'])
def cockpit_session_stop_api(cockpit_id):
    if not validate_cockpit_id(cockpit_id):
        return jsonify({
            "success": False,
            "error": "Invalid cockpit ID"
        }), 400

    success, error = stop_cockpit_session(cockpit_id)

    if not success:
        return jsonify({
            "success": False,
            "error": error
        }), 400

    return jsonify({
        "success": True,
        "cockpit_id": cockpit_id,
        "session": get_cockpit_session_state(cockpit_id)
    })


@app.route('/api/status')
def status():
    elapsed = int(time.time() - start_time) if session_active else 0
    return jsonify({
        "active": session_active,
        "autocenter": autocenter_enabled,
        "steering_sensitivity": steering_sensitivity,
        "throttle_limit": throttle_limit,
        "haptics": haptic_settings,
        "elapsed": f"{elapsed // 60:02d}:{elapsed % 60:02d}"
    })


@app.route('/vehicle/settings', methods=['GET', 'POST'])
def vehicle_settings():
    global steering_sensitivity, throttle_limit

    if request.method == 'POST':
        data = request.get_json() or {}
        if "steering_sensitivity" in data:
            steering_sensitivity = int(data["steering_sensitivity"])
        if "throttle_limit" in data:
            throttle_limit = int(data["throttle_limit"])
        return jsonify({"success": True})

    return jsonify({
        "success": True,
        "steering_sensitivity": steering_sensitivity,
        "throttle_limit": throttle_limit
    })


@app.route('/api/haptics/tune', methods=['POST'])
def tune_haptics():
    global haptic_settings
    data = request.get_json() or {}

    if "grip_threshold" in data:
        haptic_settings["grip_threshold"] = float(data["grip_threshold"])
    if "bump_sensitivity" in data:
        haptic_settings["bump_sensitivity"] = float(data["bump_sensitivity"])
    if "centering_gain" in data:
        haptic_settings["centering_gain"] = float(data["centering_gain"])
    if "alpha" in data:
        haptic_settings["alpha"] = float(data["alpha"])

    return jsonify({"success": True, "haptics": haptic_settings})


@app.route('/api/autocenter/toggle', methods=['POST'])
def toggle_autocenter():
    global autocenter_enabled
    data = request.get_json() or {}
    autocenter_enabled = data.get("enabled", not autocenter_enabled)
    set_autocenter_hardware(autocenter_enabled)
    return jsonify({"success": True, "autocenter": autocenter_enabled})


@app.route('/api/start', methods=['POST'])
def start_session():
    global session_active, start_time
    if not session_active:
        start_time = time.time()
        session_active = True
    return jsonify({"success": True, "active": True})


@app.route('/api/stop', methods=['POST'])
def stop_session():
    global session_active
    session_active = False
    return jsonify({"success": True, "active": False})


def get_current_wifi():
    try:
        output = subprocess.check_output(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"]
        ).decode().splitlines()
        for line in output:
            if line.startswith("yes:"):
                return line.split(":", 1)[1]
        return "Not Connected (AP Mode Active)"
    except Exception:
        return "Unknown"


@app.route("/wifi")
def wifi_setup():
    networks = scan_networks()
    current = get_current_wifi()
    return render_template("wifi.html", networks=networks, current_wifi=current)


@app.route("/connect", methods=["POST"])
def connect():
    ssid = request.form.get("custom_ssid") or request.form.get("ssid")
    password = request.form.get("password", "").strip()

    if not ssid:
        return jsonify({"success": False, "error": "No SSID provided"}), 400

    try:
        saved_connections = subprocess.check_output(
            ["nmcli", "-t", "-f", "NAME", "connection", "show"]
        ).decode().splitlines()

        if ssid in saved_connections and not password:
            subprocess.check_call(["sudo", "nmcli", "connection", "up", ssid])
        else:
            cmd = ["sudo", "nmcli", "device", "wifi", "connect", ssid]
            if password:
                cmd.extend(["password", password])
            subprocess.check_call(cmd)

        return render_template("wifi_success.html", ssid=ssid)

    except Exception as e:
        return render_template("wifi_error.html", error=str(e), ssid=ssid)


if __name__ == '__main__':
    initialize_cockpit_system()

    # Telemetry reception, impact detection and validated G29 impact FFB.
    telemetry_receiver.start()

    # Pi-owned cockpit session timer.
    # This does not touch Nano serial connections or the RF control loop.
    session_timer_thread = threading.Thread(
        target=cockpit_session_worker,
        daemon=True
    )
    session_timer_thread.start()

    # Multi-cockpit control. Start the same generic worker for all logical
    # cockpits. Unassigned cockpits exit safely without affecting the
    # cockpits that have valid wheel/radio pairs. RadioManager owns all Nano
    # serial connections; these workers never open Nano serial ports.
    for cockpit_id in range(1, cockpit_manager.max_cockpits + 1):
        start_cockpit_control_worker(cockpit_id)

    # Keep Flask alive while G29s are plugged/unplugged. The monitor only
    # rescans wheels when the detected wheel set changes.
    start_cockpit_device_monitor()

    try:
        app.run(host='0.0.0.0', port=5000)
    finally:
        telemetry_receiver.stop()
        stop_cockpit_device_monitor()
        stop_cockpit_control_workers()
        cockpit_manager.close()

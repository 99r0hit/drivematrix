import time
import select
import evdev
import minimal_drive
from g29_ffb import G29FFB
import threading
import subprocess
from flask import Flask, render_template, request, jsonify
from wifi_manager import monitor_wifi, scan_networks, connect_wifi
from cockpit_manager import CockpitManager


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


# Multi-cockpit wheel -> RadioManager control.
# Start with Cockpit 1 only. The same worker can be extended to Cockpits 2..4
# after this path is validated.
COCKPIT_CONTROL_INTERVAL = 0.02  # 50 Hz
COCKPIT_CONTROL_SCALE = 1000
COCKPIT_CONTROL_DEADZONE = 20
cockpit_control_stop = threading.Event()
cockpit_control_threads = {}


def get_ffb_device():
    global g29_ffb_instance
    if g29_ffb_instance is None:
        try:
            dev = evdev.InputDevice("/dev/input/event0")
            g29_ffb_instance = G29FFB(dev)
        except Exception as e:
            print(f"[FFB Error] Device access failed: {e}")
            return None
    return g29_ffb_instance


def set_autocenter_hardware(enabled: bool):
    ffb = get_ffb_device()
    if ffb:
        strength = 22000 if enabled else 0
        ffb.set_autocenter(strength)


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
    """
    Discover currently connected wheels/radios and pair the first available
    wheel/radio pairs with logical Cockpits 1..N.

    Device paths are runtime discovery information; logical cockpit IDs are
    maintained separately from /dev/input/eventX and /dev/ttyUSBX identities.
    """
    global cockpit_system_initialized

    with cockpit_init_lock:
        if cockpit_system_initialized:
            return

        wheels, radios = cockpit_manager.discover()

        if radios:
            cockpit_manager.connect_radios()
            cockpit_manager.radio_manager.discover_receivers()

        radio_ids = list(radios.keys())
        pair_count = min(
            cockpit_manager.max_cockpits,
            len(wheels),
            len(radio_ids)
        )

        for index in range(pair_count):
            cockpit_manager.assign(
                cockpit_id=index + 1,
                wheel_index=index,
                radio_id=radio_ids[index]
            )

        cockpit_system_initialized = True


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

        print(
            f"[Control] Cockpit {cockpit_id}: "
            f"wheel={cockpit.wheel.name}, "
            f"device={cockpit.wheel.path}, "
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

    return {
        "success": True,
        "cockpits": cockpits,
        "vehicles": vehicles
    }


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

    # Pi-owned cockpit session timer.
    # This does not touch Nano serial connections or the RF control loop.
    session_timer_thread = threading.Thread(
        target=cockpit_session_worker,
        daemon=True
    )
    session_timer_thread.start()

    # Multi-cockpit control. Each worker uses the already-connected
    # RadioManager; workers do NOT open Nano serial ports directly and do
    # NOT start the legacy drive worker.
    start_cockpit_control_worker(1)
    start_cockpit_control_worker(2)

    try:
        app.run(host='0.0.0.0', port=5000)
    finally:
        stop_cockpit_control_workers()
        cockpit_manager.close()

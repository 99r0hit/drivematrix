# test_force.py
import time
import glob
import evdev

def test_wheel():
    device_path = None
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = evdev.InputDevice(path)
            if evdev.ecodes.EV_FF in dev.capabilities():
                device_path = path
                print(f"✅ Found Force Feedback Wheel on {path}: {dev.name}")
                break
        except Exception:
            pass

    if not device_path:
        print("❌ No Force Feedback device found!")
        return

    dev = evdev.InputDevice(device_path)
    from g29_ffb import G29FFB

    ffb = G29FFB(dev)
    ffb.disable_autocenter()

    print("⚡ Pushing wheel LEFT (Force: -0.5)...")
    ffb.set_force(-0.5)
    time.sleep(1.5)

    print("⚡ Pushing wheel RIGHT (Force: +0.5)...")
    ffb.set_force(0.5)
    time.sleep(1.5)

    print("🛑 Stopping force.")
    ffb.stop()

if __name__ == "__main__":
    test_wheel()

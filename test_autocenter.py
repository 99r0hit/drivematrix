import time
import glob
import evdev
from g29_ffb import G29FFB

def run_test():
    device_path = None
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = evdev.InputDevice(path)
            if evdev.ecodes.EV_FF in dev.capabilities() and "Logitech" in dev.name:
                device_path = path
                break
        except Exception:
            pass

    if not device_path:
        print("❌ No Logitech FFB device found!")
        return

    dev = evdev.InputDevice(device_path)
    ffb = G29FFB(dev)

    print("⚡ Testing Auto-Center Spring Stiffness...")
    
    print("-> Low stiffness (20%)")
    ffb.set_hardware_autocenter(20)
    time.sleep(2)

    print("-> Maximum stiffness (100%) - Turn wheel to feel resistance")
    ffb.set_hardware_autocenter(100)
    time.sleep(3)

    print("-> Turning off spring (0%)")
    ffb.set_hardware_autocenter(0)
    ffb.stop()
    print("✅ Test complete.")

if __name__ == "__main__":
    run_test()

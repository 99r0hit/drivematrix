import evdev
from evdev import ecodes
import glob

def find_wheel_device():
    for dev_path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = evdev.InputDevice(dev_path)
            if ecodes.EV_FF in dev.capabilities():
                print(f"✅ Found FFB Device: {dev.name} ({dev_path})")
                return dev
        except Exception:
            pass
    return None

class G29FFB:
    def __init__(self, dev=None):
        self.dev = dev if dev else find_wheel_device()
        if not self.dev:
            raise RuntimeError("❌ No FFB-capable device found on any event node!")

    def disable_autocenter(self):
        try:
            self.dev.write(ecodes.EV_FF, ecodes.FF_AUTOCENTER, 0)
        except Exception as e:
            print(f"⚠️ [FFB Engine] Auto-center disable failed: {e}")

    def set_hardware_autocenter(self, strength_pct):
        """Sets the G29 internal centering spring force level (0 to 100%)."""
        try:
            magnitude = int(max(0.0, min(100.0, float(strength_pct))) * 655.35)
            self.dev.write(ecodes.EV_FF, ecodes.FF_AUTOCENTER, magnitude)
        except Exception as e:
            print(f"❌ [FFB Engine] Auto-center weight update failed: {e}")

    def set_autocenter(self, strength_pct):
        """Alias for app.py compatibility."""
        self.set_hardware_autocenter(strength_pct)

    def set_force(self, force_val):
        self.set_hardware_autocenter(abs(force_val) * 100.0)

    def stop(self):
        try:
            self.set_hardware_autocenter(0)
        except Exception:
            pass

import glob
from evdev import InputDevice, ecodes

class FFBManager:
    def __init__(self):
        self.device = self._find_wheel_device()
        self.last_pct = -1

    def _find_wheel_device(self):
        """Locates the force-feedback capable steering wheel input event device."""
        for dev_path in sorted(glob.glob("/dev/input/event*")):
            try:
                dev = InputDevice(dev_path)
                capabilities = dev.capabilities()
                if ecodes.EV_FF in capabilities:
                    print(f"✅ [FFB Driver] Found G29 Hardware: {dev.name} at {dev_path}", flush=True)
                    return dev
            except Exception:
                pass
        print("⚠️ [FFB Driver] No Force Feedback device found.", flush=True)
        return None

    def set_hardware_autocenter(self, strength_pct):
        """Dynamic spring stiffness adjustment with USB bus throttling."""
        if not self.device:
            return

        pct = int(max(0.0, min(100.0, strength_pct)))
        
        # Zero out force instantly when disabled
        if pct == 0:
            if self.last_pct != 0:
                self.last_pct = 0
                try:
                    self.device.write(ecodes.EV_FF, ecodes.FF_AUTOCENTER, 0)
                except Exception:
                    pass
            return

        # Skip write if value hasn't shifted by at least 3% to prevent queue lag
        if abs(pct - self.last_pct) < 3:
            return
            
        self.last_pct = pct
        raw_val = int((pct / 100.0) * 65535)

        try:
            self.device.write(ecodes.EV_FF, ecodes.FF_AUTOCENTER, raw_val)
        except Exception as e:
            print(f"❌ [FFB Driver] Write Error: {e}", flush=True)


    def set_hardware_force(self, force_input):
        """Alias method to accept floats (-1.0 to 1.0) or percentages (0-100)."""
        if isinstance(force_input, float) and abs(force_input) <= 1.0:
            pct = abs(force_input) * 100.0
        else:
            pct = force_input
        self.set_hardware_autocenter(pct)

    def trigger_transient_kick(self, pulse_strength=100, duration_sec=0.15):
        """Executes a momentary force surge for impacts."""
        if not self.device:
            return

        current_pct = self.last_pct
        self.set_hardware_autocenter(pulse_strength)
        import time
        time.sleep(duration_sec)
        self.set_hardware_autocenter(max(0, current_pct))

import evdev
from evdev import ecodes
import glob


G29_NAME = "Logitech G29 Driving Force Racing Wheel"


class G29Device:
    def __init__(self, path):
        self.path = path
        self.device = evdev.InputDevice(path)

        self.name = self.device.name
        self.phys = self.device.phys
        self.uniq = self.device.uniq

        self.capabilities = self.device.capabilities()

    def __repr__(self):
        return (
            f"G29Device("
            f"path={self.path}, "
            f"name={self.name!r}, "
            f"phys={self.phys!r}, "
            f"uniq={self.uniq!r})"
        )


def discover_g29s():
    """
    Discover all currently connected Logitech G29 wheels.

    The returned list is only a discovery result.
    It must NOT be treated as a permanent cockpit assignment.
    """

    wheels = []

    for path in sorted(glob.glob("/dev/input/event*")):

        try:
            dev = evdev.InputDevice(path)

            if dev.name != G29_NAME:
                dev.close()
                continue

            wheel = G29Device(path)
            wheels.append(wheel)

        except Exception:
            pass

    return wheels


def has_ffb(wheel):
    """Return True if the wheel supports force feedback."""

    return ecodes.EV_FF in wheel.capabilities


def print_g29s(wheels):
    print("=" * 70)
    print(f"G29 DISCOVERY")
    print(f"Found {len(wheels)} G29 wheel(s)")
    print("=" * 70)

    for index, wheel in enumerate(wheels, start=1):

        print(
            f"Wheel {index}\n"
            f"  Event : {wheel.path}\n"
            f"  Name  : {wheel.name}\n"
            f"  Phys  : {wheel.phys}\n"
            f"  Uniq  : {wheel.uniq or '(none)'}\n"
            f"  FFB   : {'YES' if has_ffb(wheel) else 'NO'}"
        )

    print("=" * 70)


def close_g29s(wheels):
    for wheel in wheels:
        try:
            wheel.device.close()
        except Exception:
            pass


if __name__ == "__main__":

    wheels = discover_g29s()

    try:
        print_g29s(wheels)
    finally:
        close_g29s(wheels)

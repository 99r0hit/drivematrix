#!/usr/bin/env python3

from g29_discovery import (
    discover_g29s,
    has_ffb,
    close_g29s
)


class WheelManager:
    """
    Manages currently connected Logitech G29 wheels.

    Important:
        /dev/input/eventX is a runtime device path.
        It is NOT a permanent wheel identity.

    This class currently handles discovery only.
    Dynamic cockpit assignment will be added later.
    """

    def __init__(self):

        self.wheels = []

    # ========================================================
    # DISCOVERY
    # ========================================================

    def discover(self):
        """
        Discover all currently connected G29 wheels.

        Returns:
            list[G29Device]
        """

        # Close previous discovery objects first.
        self.close()

        self.wheels = discover_g29s()

        print(
            f"[WheelManager] Found "
            f"{len(self.wheels)} G29 wheel(s)"
        )

        return self.wheels

    # ========================================================
    # COUNT
    # ========================================================

    def count(self):

        return len(
            self.wheels
        )

    # ========================================================
    # GET ALL
    # ========================================================

    def get_wheels(self):

        return self.wheels

    # ========================================================
    # GET BY INDEX
    # ========================================================

    def get_wheel(
        self,
        index
    ):

        if index < 0:
            return None

        if index >= len(
            self.wheels
        ):
            return None

        return self.wheels[index]

    # ========================================================
    # FFB SUPPORT
    # ========================================================

    def all_have_ffb(self):

        if not self.wheels:

            return False

        return all(
            has_ffb(wheel)
            for wheel in self.wheels
        )

    # ========================================================
    # PRINT STATUS
    # ========================================================

    def print_status(self):

        print()
        print(
            "=============================="
        )
        print(
            "DriveMatrix Wheel Manager"
        )
        print(
            "=============================="
        )

        print(
            f"Wheels: "
            f"{len(self.wheels)}"
        )

        print()

        if not self.wheels:

            print(
                "No G29 wheels discovered."
            )

            return

        for index, wheel in enumerate(
            self.wheels,
            start=1
        ):

            print(
                f"Wheel {index}"
            )

            print(
                f"  Event : "
                f"{wheel.path}"
            )

            print(
                f"  Name  : "
                f"{wheel.name}"
            )

            print(
                f"  Phys  : "
                f"{wheel.phys or '(none)'}"
            )

            print(
                f"  Uniq  : "
                f"{wheel.uniq or '(none)'}"
            )

            print(
                f"  FFB   : "
                f"{'YES' if has_ffb(wheel) else 'NO'}"
            )

            print()

    # ========================================================
    # CLOSE
    # ========================================================

    def close(self):

        if self.wheels:

            close_g29s(
                self.wheels
            )

        self.wheels = []


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":

    manager = WheelManager()

    try:

        print()
        print(
            "=============================="
        )
        print(
            "DriveMatrix Wheel Manager Test"
        )
        print(
            "=============================="
        )

        wheels = manager.discover()

        manager.print_status()

        print()

        if not wheels:

            print(
                "RESULT: NO_G29"
            )

        elif not manager.all_have_ffb():

            print(
                "RESULT: G29_FFB_CHECK_FAILED"
            )

        else:

            print(
                "RESULT: G29_DISCOVERY_PASS"
            )

    finally:

        manager.close()

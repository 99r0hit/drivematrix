#!/usr/bin/env python3

from dataclasses import dataclass

from wheel_manager import WheelManager
from radio_manager import RadioManager


MAX_COCKPITS = 4


@dataclass
class Cockpit:
    cockpit_id: int
    wheel: object = None
    radio_id: str = None
    vehicle_name: str = None

    @property
    def assigned(self):
        return self.wheel is not None and self.radio_id is not None


class CockpitManager:
    """
    Manages the logical relationship between DriveMatrix cockpits,
    discovered G29 wheels, and discovered Nano radios.

    Important:
        - Cockpit IDs are logical.
        - G29 event paths are runtime paths.
        - Nano radio IDs are permanent hardware identities.
        - Vehicle assignment is delegated to RadioManager.
        - RF target selection is handled by RadioManager/RadioController.
        - No direct RF protocol logic exists in this layer.
    """

    def __init__(self, max_cockpits=MAX_COCKPITS):
        if max_cockpits < 1:
            raise ValueError("max_cockpits must be at least 1")

        self.max_cockpits = max_cockpits

        self.wheel_manager = WheelManager()
        self.radio_manager = RadioManager()

        self.cockpits = {
            cockpit_id: Cockpit(cockpit_id)
            for cockpit_id in range(1, max_cockpits + 1)
        }

    # ========================================================
    # DISCOVERY
    # ========================================================

    def discover(self):
        """
        Discover currently connected G29 wheels and Nano radios.

        This performs discovery only. It does not assign anything
        and does not select a vehicle or send control packets.
        """

        wheels = self.wheel_manager.discover()
        radios = self.radio_manager.discover()

        return wheels, radios

    # ========================================================
    # RADIO CONNECTION
    # ========================================================

    def connect_radios(self):
        """
        Connect all radios already discovered by RadioManager.
        """

        return self.radio_manager.connect_all()

    # ========================================================
    # COCKPIT ASSIGNMENT
    # ========================================================

    def assign_wheel(self, cockpit_id, wheel_index):
        """Assign only a wheel to a logical cockpit."""
        cockpit = self.cockpits.get(cockpit_id)
        if cockpit is None:
            raise ValueError(f"Invalid cockpit ID: {cockpit_id}")

        wheel = self.wheel_manager.get_wheel(wheel_index)
        if wheel is None:
            raise ValueError(f"Invalid wheel index: {wheel_index}")

        for other_id, other in self.cockpits.items():
            if other_id != cockpit_id and other.wheel is wheel:
                raise ValueError(f"Wheel is already assigned to Cockpit {other_id}")

        cockpit.wheel = wheel
        return cockpit

    def assign_radio(self, cockpit_id, radio_id):
        """Assign only a Nano radio to a logical cockpit."""
        cockpit = self.cockpits.get(cockpit_id)
        if cockpit is None:
            raise ValueError(f"Invalid cockpit ID: {cockpit_id}")

        radio = self.radio_manager.get_radio(radio_id)
        if radio is None:
            raise ValueError(f"Unknown radio ID: {radio_id}")

        for other_id, other in self.cockpits.items():
            if other_id != cockpit_id and other.radio_id == radio_id:
                raise ValueError(
                    f"Radio {radio_id} is already assigned to Cockpit {other_id}"
                )

        cockpit.radio_id = radio_id
        return cockpit

    def assign(self, cockpit_id, wheel_index, radio_id):
        """Assign one discovered G29 and one discovered Nano radio."""
        cockpit = self.cockpits.get(cockpit_id)
        if cockpit is None:
            raise ValueError(f"Invalid cockpit ID: {cockpit_id}")

        self.assign_wheel(cockpit_id, wheel_index)
        try:
            self.assign_radio(cockpit_id, radio_id)
        except Exception:
            # Do not leave a partial assignment if the radio assignment fails.
            cockpit.wheel = None
            raise

        return cockpit

    def select_vehicle(self, cockpit_id, vehicle_name):
        """
        Dynamically select a vehicle for the Nano assigned to a cockpit.

        Vehicle lookup, RX discovery, duplicate-target protection, and
        TARGET selection are handled by RadioManager.
        """

        cockpit = self.cockpits.get(cockpit_id)

        if cockpit is None:
            raise ValueError(
                f"Invalid cockpit ID: {cockpit_id}"
            )

        if cockpit.wheel is None:
            raise ValueError(
                f"Cockpit {cockpit_id} has no wheel assigned"
            )

        if cockpit.radio_id is None:
            raise ValueError(
                f"Cockpit {cockpit_id} has no radio assigned"
            )

        success = self.radio_manager.select_vehicle(
            cockpit.radio_id,
            vehicle_name
        )

        if success:
            cockpit.vehicle_name = str(vehicle_name).strip()

        return success

    def clear_vehicle(self, cockpit_id):
        """
        Clear the currently selected vehicle from a cockpit.
        """

        cockpit = self.cockpits.get(cockpit_id)

        if cockpit is None:
            raise ValueError(
                f"Invalid cockpit ID: {cockpit_id}"
            )

        if cockpit.radio_id is None:
            raise ValueError(
                f"Cockpit {cockpit_id} has no radio assigned"
            )

        success = self.radio_manager.clear_vehicle(
            cockpit.radio_id
        )

        if success:
            cockpit.vehicle_name = None

        return success

    def clear(self, cockpit_id):
        """
        Remove the wheel/radio assignment from one cockpit.

        Vehicle selection is also cleared from the logical cockpit state.
        """

        cockpit = self.cockpits.get(cockpit_id)

        if cockpit is None:
            raise ValueError(
                f"Invalid cockpit ID: {cockpit_id}"
            )

        cockpit.wheel = None
        cockpit.radio_id = None
        cockpit.vehicle_name = None

    # ========================================================
    # GETTERS
    # ========================================================

    def get_cockpit(self, cockpit_id):
        return self.cockpits.get(cockpit_id)

    def get_assigned_cockpits(self):
        return {
            cockpit_id: cockpit
            for cockpit_id, cockpit in self.cockpits.items()
            if cockpit.assigned
        }

    # ========================================================
    # STATUS
    # ========================================================

    def print_status(self):
        print()
        print("=" * 70)
        print("DriveMatrix Cockpit Manager")
        print("=" * 70)

        for cockpit_id in range(1, self.max_cockpits + 1):
            cockpit = self.cockpits[cockpit_id]

            print()
            print(f"Cockpit {cockpit_id}")

            if cockpit.wheel is None:
                print("  Wheel : UNASSIGNED")
            else:
                print(f"  Wheel : {cockpit.wheel.path}")
                print(f"  Phys  : {cockpit.wheel.phys or '(none)'}")

            if cockpit.radio_id is None:
                print("  Radio : UNASSIGNED")
            else:
                radio = self.radio_manager.get_radio(
                    cockpit.radio_id
                )

                print(f"  Radio : {cockpit.radio_id}")

                if radio is not None:
                    print(
                        f"  Port  : {radio.port}"
                    )
                    print(
                        f"  Conn  : "
                        f"{'YES' if radio.connected else 'NO'}"
                    )

            if cockpit.vehicle_name is None:
                print("  Vehicle: UNASSIGNED")
            else:
                print(f"  Vehicle: {cockpit.vehicle_name}")

        print()
        print("=" * 70)

    # ========================================================
    # CLOSE
    # ========================================================

    def close(self):
        self.wheel_manager.close()
        self.radio_manager.disconnect_all()


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":

    manager = CockpitManager()

    try:
        print()
        print("==============================")
        print("DriveMatrix Cockpit Manager Test")
        print("==============================")

        # ----------------------------------------------------
        # Discover wheels and radios
        # ----------------------------------------------------

        print()
        print("DISCOVERY")

        wheels, radios = manager.discover()

        print()
        print(
            f"Wheels discovered : {len(wheels)}"
        )
        print(
            f"Radios discovered : {len(radios)}"
        )

        # ----------------------------------------------------
        # Connect radios
        # ----------------------------------------------------

        if radios:
            print()
            print("RADIO CONNECTION")

            connected = manager.connect_radios()

            print(
                f"Connected radios  : "
                f"{connected}/{len(radios)}"
            )

        # ----------------------------------------------------
        # Print currently unassigned logical cockpits
        # ----------------------------------------------------

        print()
        print("INITIAL COCKPIT STATE")

        manager.print_status()

        # ----------------------------------------------------
        # Test only discovery/state.
        #
        # We deliberately do NOT auto-pair devices here because
        # G29 event paths are runtime paths and can change.
        # ----------------------------------------------------

        print()
        print("ASSIGNMENT CHECK")

        if len(wheels) >= 2 and len(radios) >= 2:
            radio_ids = list(radios.keys())

            manager.assign(
                cockpit_id=1,
                wheel_index=0,
                radio_id=radio_ids[0]
            )

            manager.assign(
                cockpit_id=2,
                wheel_index=1,
                radio_id=radio_ids[1]
            )

            print("Cockpit 1 assignment: OK")
            print("Cockpit 2 assignment: OK")

            manager.print_status()

            # ------------------------------------------------
            # Vehicle discovery
            # ------------------------------------------------

            print()
            print("VEHICLE DISCOVERY")

            receivers = manager.radio_manager.discover_receivers()

            if len(receivers) < 2:
                print(
                    "Need at least 2 discovered receivers "
                    "for the dynamic swap test."
                )
                print()
                print("RESULT: INSUFFICIENT_VEHICLES")
            else:
                vehicle_names = sorted(
                    receiver["vehicle_name"]
                    for receiver in receivers.values()
                    if receiver.get("vehicle_name")
                )

                print()
                print("INITIAL VEHICLE ASSIGNMENT")

                if not manager.select_vehicle(1, vehicle_names[0]):
                    raise RuntimeError(
                        f"Failed to select {vehicle_names[0]} "
                        "for Cockpit 1"
                    )

                if not manager.select_vehicle(2, vehicle_names[1]):
                    raise RuntimeError(
                        f"Failed to select {vehicle_names[1]} "
                        "for Cockpit 2"
                    )

                print(
                    f"Cockpit 1 -> {vehicle_names[0]}: OK"
                )
                print(
                    f"Cockpit 2 -> {vehicle_names[1]}: OK"
                )

                active = manager.radio_manager.get_active_vehicles()

                if set(active.keys()) != set(vehicle_names[:2]):
                    raise RuntimeError(
                        "Initial active vehicle mapping is incorrect"
                    )

                print()
                print("DYNAMIC VEHICLE SWAP")

                # Swap the cars between the two already-assigned Nanos.
                if not manager.select_vehicle(1, vehicle_names[1]):
                    raise RuntimeError(
                        f"Failed to swap Cockpit 1 to {vehicle_names[1]}"
                    )

                if not manager.select_vehicle(2, vehicle_names[0]):
                    raise RuntimeError(
                        f"Failed to swap Cockpit 2 to {vehicle_names[0]}"
                    )

                cockpit1 = manager.get_cockpit(1)
                cockpit2 = manager.get_cockpit(2)

                if cockpit1.vehicle_name != vehicle_names[1]:
                    raise RuntimeError(
                        "Cockpit 1 vehicle state did not update after swap"
                    )

                if cockpit2.vehicle_name != vehicle_names[0]:
                    raise RuntimeError(
                        "Cockpit 2 vehicle state did not update after swap"
                    )

                active = manager.radio_manager.get_active_vehicles()

                if (
                    active.get(vehicle_names[1], {}).get("radio_id")
                    != cockpit1.radio_id
                ):
                    raise RuntimeError(
                        "Car 2 is not mapped to Cockpit 1 radio after swap"
                    )

                if (
                    active.get(vehicle_names[0], {}).get("radio_id")
                    != cockpit2.radio_id
                ):
                    raise RuntimeError(
                        "Car 1 is not mapped to Cockpit 2 radio after swap"
                    )

                print(
                    f"Cockpit 1 -> {vehicle_names[1]}: OK"
                )
                print(
                    f"Cockpit 2 -> {vehicle_names[0]}: OK"
                )
                print("Dynamic vehicle swap: OK")

                manager.print_status()

            assigned = manager.get_assigned_cockpits()

            if len(assigned) == 2:
                print()
                print("RESULT: TEST_COMPLETE")
            else:
                print()
                print("RESULT: ASSIGNMENT_FAILED")

    except Exception as exc:
        print()
        print(f"ERROR: {exc}")
        print()
        print("RESULT: TEST_FAILED")

    finally:
        manager.close()

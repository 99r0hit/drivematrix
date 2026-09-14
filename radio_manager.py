import time

from radio_discovery import discover_radios
from radio_controller import RadioController
from vehicle_registry import VehicleRegistry


class RadioManager:
    """
    Manages multiple DriveMatrix Nano + nRF24 radios.

    Permanent identities:
        Nano Radio ID -> RF-XXXXXX
        RX ID         -> ESP32 factory MAC
        RX ID         -> Vehicle name

    Dynamic session relationship:
        Nano Radio ID -> currently selected RX / vehicle

    A Nano is NOT permanently assigned to a vehicle.
    """

    def __init__(self):

        self.radios = {}
        self.vehicle_registry = VehicleRegistry()
        self.receivers = {}

    # ========================================================
    # DISCOVER NANOS
    # ========================================================

    def discover(self):
        """Discover Nano radios while preserving existing controllers."""
        discovered = discover_radios()
        discovered_ids = set()

        for radio in discovered:
            radio_id = str(radio.radio_id).strip()
            discovered_ids.add(radio_id)
            existing = self.radios.get(radio_id)

            if existing is not None:
                # Same permanent Nano identity: keep the live controller so
                # refresh does not unnecessarily reset the serial connection.
                if existing.port == radio.port:
                    continue

                # Same Nano appeared on a different USB port. Replace its
                # controller cleanly because the old serial endpoint changed.
                try:
                    existing.disconnect()
                except Exception:
                    pass

            controller = RadioController(
                port=radio.port,
                radio_id=radio_id
            )
            controller.receiver_id = None if existing is None else existing.receiver_id
            controller.vehicle_name = None if existing is None else existing.vehicle_name
            self.radios[radio_id] = controller

        # Remove Nanos that are no longer physically present.
        for radio_id in list(self.radios.keys()):
            if radio_id in discovered_ids:
                continue
            controller = self.radios.pop(radio_id)
            try:
                controller.disconnect()
            except Exception:
                pass
            print(f"[RadioManager] Radio disconnected: {radio_id}")

        print(f"[RadioManager] Found {len(self.radios)} radio(s)")
        return self.radios

    # ========================================================
    # CONNECT ALL
    # ========================================================

    def connect_all(self):

        connected = 0

        for (
            radio_id,
            controller
        ) in self.radios.items():

            if controller.connected:
                connected += 1
                continue

            print(
                f"[RadioManager] Connecting "
                f"{radio_id}..."
            )

            if controller.connect():
                connected += 1

        print(
            f"[RadioManager] Connected "
            f"{connected}/{len(self.radios)} radio(s)"
        )

        return connected

    # ========================================================
    # DISCOVER ALL RECEIVERS
    # ========================================================

    def discover_receivers(self):
        """
        Discover all RXs visible to all connected Nanos.

        Discovery does not create a Nano -> RX permanent
        relationship.

        RX ID -> Vehicle name comes from VehicleRegistry.
        """

        self.receivers.clear()

        for (
            radio_id,
            controller
        ) in self.radios.items():

            if not controller.connected:

                print(
                    f"[RadioManager] "
                    f"{radio_id} is not connected"
                )

                continue

            print()
            print(
                f"[RadioManager] Discovering RXs "
                f"using {radio_id}..."
            )

            discovered = (
                controller.discover_receivers()
            )

            if not discovered:

                print(
                    "  No receivers found."
                )

                continue

            for (
                receiver_id,
                reported_name
            ) in discovered.items():

                receiver_id = (
                    receiver_id.strip().upper()
                )

                # ------------------------------------------------
                # Existing permanent vehicle
                # ------------------------------------------------

                vehicle = (
                    self.vehicle_registry.get_vehicle(
                        receiver_id
                    )
                )

                if vehicle:

                    vehicle_name = (
                        vehicle.get("name")
                    )

                    print(
                        f"  RX {receiver_id} "
                        f"-> registered "
                        f"{vehicle_name}"
                    )

                # ------------------------------------------------
                # New receiver
                # ------------------------------------------------

                else:

                    print(
                        f"  RX {receiver_id} "
                        f"-> not registered"
                    )

                    vehicle = (
                        self.vehicle_registry.assign_next(
                            receiver_id
                        )
                    )

                    vehicle_name = (
                        vehicle.get("name")
                    )

                    print(
                        f"  NEW assignment: "
                        f"{vehicle_name}"
                    )

                # ------------------------------------------------
                # Store globally.
                # ------------------------------------------------

                if receiver_id not in self.receivers:

                    self.receivers[
                        receiver_id
                    ] = {
                        "vehicle_name":
                            vehicle_name,

                        "reported_name":
                            reported_name
                    }

        print()
        print(
            "=============================="
        )
        print(
            "DriveMatrix Receiver Discovery"
        )
        print(
            "=============================="
        )

        print(
            f"Unique RXs discovered: "
            f"{len(self.receivers)}"
        )

        for (
            receiver_id,
            receiver
        ) in sorted(
            self.receivers.items()
        ):

            print(
                f"  {receiver['vehicle_name']}"
                f" -> {receiver_id}"
            )

        print()

        return self.receivers

    # ========================================================
    # FIND VEHICLE BY NAME
    # ========================================================

    def _find_vehicle_by_name(
        self,
        vehicle_name
    ):
        """
        VehicleRegistry stores:

            RX_ID -> {"name": "Car N"}

        Find the RX ID belonging to a vehicle name.
        """

        vehicle_name = (
            str(vehicle_name).strip()
        )

        for (
            receiver_id,
            vehicle
        ) in self.vehicle_registry.vehicles.items():

            if not isinstance(
                vehicle,
                dict
            ):
                continue

            if (
                str(
                    vehicle.get("name", "")
                ).strip()
                == vehicle_name
            ):

                return {
                    "receiver_id":
                        receiver_id,

                    "name":
                        vehicle_name
                }

        return None

    # ========================================================
    # SELECT VEHICLE
    # ========================================================

    def select_vehicle(
        self,
        radio_id,
        vehicle_name
    ):
        """
        Dynamically assign a vehicle to a Nano.

        Example:

            RF-BC035F -> Car 1

        Sends:

            TARGET,<RX_ID>

        to the Nano.
        """

        radio_id = (
            str(radio_id).strip()
        )

        vehicle_name = (
            str(vehicle_name).strip()
        )

        controller = self.get_radio(
            radio_id
        )

        if not controller:

            print(
                f"[RadioManager] Unknown radio: "
                f"{radio_id}"
            )

            return False

        if not controller.connected:

            print(
                f"[RadioManager] Radio not connected: "
                f"{radio_id}"
            )

            return False

        # ----------------------------------------------------
        # Find permanent vehicle -> RX mapping.
        # ----------------------------------------------------

        vehicle = (
            self._find_vehicle_by_name(
                vehicle_name
            )
        )

        if not vehicle:

            print(
                f"[RadioManager] Vehicle not found: "
                f"{vehicle_name}"
            )

            return False

        receiver_id = (
            vehicle["receiver_id"]
        )

        print()
        print(
            f"[RadioManager] Selecting "
            f"{vehicle_name} for {radio_id}"
        )

        print(
            f"  RX ID: "
            f"{receiver_id}"
        )

        # ----------------------------------------------------
        # Make sure this Nano can see the RX.
        # ----------------------------------------------------

        if (
            receiver_id
            not in controller.discovered_receivers
        ):

            print(
                "  RX not in current Nano "
                "discovery list."
            )

            print(
                "  Running discovery..."
            )

            discovered = (
                controller.discover_receivers()
            )

            if (
                receiver_id
                not in discovered
            ):

                print(
                    f"  RX not reachable: "
                    f"{receiver_id}"
                )

                return False

        # ----------------------------------------------------
        # Prevent two Nanos controlling same RX.
        # ----------------------------------------------------

        existing_radio = (
            self.get_radio_by_receiver(
                receiver_id
            )
        )

        if (
            existing_radio
            and existing_radio is not controller
        ):

            print(
                f"[RadioManager] RX "
                f"{receiver_id} already assigned "
                f"to radio "
                f"{existing_radio.radio_id}"
            )

            return False

        # ----------------------------------------------------
        # Select target.
        # ----------------------------------------------------

        if not controller.select_target(
            receiver_id
        ):

            print(
                f"[RadioManager] TARGET selection "
                f"failed for {receiver_id}"
            )

            return False

        # ----------------------------------------------------
        # Store dynamic relationship.
        # ----------------------------------------------------

        controller.receiver_id = (
            receiver_id
        )

        controller.vehicle_name = (
            vehicle_name
        )

        print(
            f"[RadioManager] ACTIVE: "
            f"{radio_id} -> "
            f"{vehicle_name}"
        )

        return True

    # ========================================================
    # CLEAR VEHICLE
    # ========================================================

    def clear_vehicle(
        self,
        radio_id
    ):

        controller = self.get_radio(
            radio_id
        )

        if not controller:

            return False

        controller.clear_target()

        controller.receiver_id = None
        controller.vehicle_name = None

        print(
            f"[RadioManager] Cleared vehicle "
            f"assignment for {radio_id}"
        )

        return True

    # ========================================================
    # GET ACTIVE VEHICLES
    # ========================================================

    def get_active_vehicles(self):

        vehicles = {}

        for (
            radio_id,
            controller
        ) in self.radios.items():

            if not controller.connected:
                continue

            if not controller.receiver_id:
                continue

            if not controller.vehicle_name:
                continue

            vehicles[
                controller.vehicle_name
            ] = {

                "vehicle_name":
                    controller.vehicle_name,

                "receiver_id":
                    controller.receiver_id,

                "radio_id":
                    radio_id,

                "port":
                    controller.port,

                "connected":
                    controller.connected
            }

        return vehicles

    # ========================================================
    # GET ACTIVE VEHICLE
    # ========================================================

    def get_active_vehicle(
        self,
        vehicle_name
    ):

        return (
            self.get_active_vehicles()
            .get(vehicle_name)
        )

    # ========================================================
    # GET RADIO
    # ========================================================

    def get_radio(
        self,
        radio_id
    ):

        return self.radios.get(
            radio_id
        )

    # ========================================================
    # GET RADIO BY RECEIVER
    # ========================================================

    def get_radio_by_receiver(
        self,
        receiver_id
    ):

        receiver_id = (
            str(receiver_id)
            .strip()
            .upper()
        )

        for controller in (
            self.radios.values()
        ):

            if (
                controller.receiver_id
                == receiver_id
            ):

                return controller

        return None

    # ========================================================
    # GET RADIO BY VEHICLE
    # ========================================================

    def get_radio_by_vehicle(
        self,
        vehicle_name
    ):

        vehicle_name = (
            str(vehicle_name).strip()
        )

        for controller in (
            self.radios.values()
        ):

            if (
                controller.vehicle_name
                == vehicle_name
            ):

                return controller

        return None

    # ========================================================
    # SEND CONTROL BY RADIO
    # ========================================================

    def send_control(
        self,
        radio_id,
        steering,
        throttle
    ):

        controller = self.get_radio(
            radio_id
        )

        if not controller:

            print(
                f"[RadioManager] Unknown radio: "
                f"{radio_id}"
            )

            return False

        return controller.send_control(
            steering,
            throttle
        )

    # ========================================================
    # SEND CONTROL BY VEHICLE
    # ========================================================

    def send_vehicle_control(
        self,
        vehicle_name,
        steering,
        throttle
    ):

        controller = (
            self.get_radio_by_vehicle(
                vehicle_name
            )
        )

        if not controller:

            print(
                f"[RadioManager] "
                f"Active vehicle not found: "
                f"{vehicle_name}"
            )

            return False

        return controller.send_control(
            steering,
            throttle
        )

    # ========================================================
    # PRINT ACTIVE VEHICLES
    # ========================================================

    def print_active_vehicles(self):

        vehicles = (
            self.get_active_vehicles()
        )

        print()
        print(
            "=============================="
        )
        print(
            "DriveMatrix Active Vehicles"
        )
        print(
            "=============================="
        )

        print(
            f"Active vehicles: "
            f"{len(vehicles)}"
        )

        print()

        if not vehicles:

            print(
                "No active vehicles."
            )

            return

        for vehicle_name in sorted(
            vehicles
        ):

            vehicle = vehicles[
                vehicle_name
            ]

            print(
                f"Vehicle: "
                f"{vehicle['vehicle_name']}"
            )

            print(
                f"  RX ID:     "
                f"{vehicle['receiver_id']}"
            )

            print(
                f"  Radio ID:  "
                f"{vehicle['radio_id']}"
            )

            print(
                f"  Port:      "
                f"{vehicle['port']}"
            )

            print(
                f"  Connected: "
                f"{vehicle['connected']}"
            )

            print()

    # ========================================================
    # PRINT STATUS
    # ========================================================

    def print_status(self):

        print()
        print(
            "=============================="
        )
        print(
            "DriveMatrix Radio Manager"
        )
        print(
            "=============================="
        )

        print(
            f"Radios: "
            f"{len(self.radios)}"
        )

        print()

        for (
            radio_id,
            controller
        ) in self.radios.items():

            print(
                f"Radio ID: {radio_id}"
            )

            print(
                f"  Port:      "
                f"{controller.port}"
            )

            print(
                f"  Connected: "
                f"{controller.connected}"
            )

            print(
                f"  RX ID:     "
                f"{controller.receiver_id}"
            )

            print(
                f"  Vehicle:   "
                f"{controller.vehicle_name}"
            )

            print(
                f"  Target:    "
                f"{controller.selected_receiver_id}"
            )

            print(
                f"  Last TX:   "
                f"{controller.last_tx_time}"
            )

            print(
                f"  Last ACK:  "
                f"{controller.last_ack_time}"
            )

            print()

    # ========================================================
    # DISCONNECT ALL
    # ========================================================

    def disconnect_all(self):

        for controller in (
            self.radios.values()
        ):

            controller.disconnect()

    # ========================================================
    # TEST
    # ========================================================

    def test(self):

        print()
        print(
            "=============================="
        )
        print(
            "DriveMatrix Radio Manager Test"
        )
        print(
            "=============================="
        )

        # ----------------------------------------------------
        # 1. Discover Nanos
        # ----------------------------------------------------

        print()
        print(
            "NANO DISCOVERY"
        )

        radios = self.discover()

        if not radios:

            print(
                "RESULT: NO_RADIOS"
            )

            return False

        # ----------------------------------------------------
        # 2. Connect
        # ----------------------------------------------------

        print()
        print(
            "CONNECT"
        )

        connected = (
            self.connect_all()
        )

        if connected != len(
            self.radios
        ):

            print(
                "RESULT: CONNECT_INCOMPLETE"
            )

            self.disconnect_all()

            return False

        # ----------------------------------------------------
        # 3. Discover RXs
        # ----------------------------------------------------

        print()
        print(
            "RX DISCOVERY"
        )

        receivers = (
            self.discover_receivers()
        )

        if not receivers:

            print(
                "RESULT: NO_RECEIVERS"
            )

            self.disconnect_all()

            return False

        # ----------------------------------------------------
        # 4. Persistent registry
        # ----------------------------------------------------

        print()
        print(
            "PERSISTENT VEHICLE REGISTRY"
        )

        self.vehicle_registry.print_all()

        # ----------------------------------------------------
        # 5. Discovered receivers
        # ----------------------------------------------------

        print()
        print(
            "DISCOVERED RECEIVERS"
        )

        for (
            receiver_id,
            receiver
        ) in sorted(
            receivers.items()
        ):

            print(
                f"  {receiver['vehicle_name']}"
                f" -> {receiver_id}"
            )

        # ----------------------------------------------------
        # 6. Dynamic assignment
        # ----------------------------------------------------

        print()
        print(
            "DYNAMIC VEHICLE ASSIGNMENT"
        )

        vehicle_names = sorted(
            set(
                receiver[
                    "vehicle_name"
                ]
                for receiver in
                receivers.values()
            )
        )

        radio_items = list(
            self.radios.items()
        )

        assignment_count = min(
            len(vehicle_names),
            len(radio_items)
        )

        for i in range(
            assignment_count
        ):

            radio_id, controller = (
                radio_items[i]
            )

            vehicle_name = (
                vehicle_names[i]
            )

            print()
            print(
                f"Assigning "
                f"{vehicle_name} -> "
                f"{radio_id}"
            )

            if not self.select_vehicle(
                radio_id,
                vehicle_name
            ):

                print(
                    "RESULT: "
                    "TARGET_ASSIGNMENT_FAILED"
                )

                self.disconnect_all()

                return False

        # ----------------------------------------------------
        # 7. Active vehicles
        # ----------------------------------------------------

        self.print_active_vehicles()

        # ----------------------------------------------------
        # 8. Control + ACK test
        #
        # Two packets are deliberately sent.
        #
        # Packet 1:
        #   normally has no ACK payload.
        #
        # Packet 2:
        #   should receive ACK payload generated by RX
        #   after packet 1.
        # ----------------------------------------------------

        print()
        print(
            "CONTROL TEST"
        )

        for (
            radio_id,
            controller
        ) in radio_items[
            :assignment_count
        ]:

            print()
            print(
                f"Testing {radio_id}"
            )

            print(
                f"  RX: "
                f"{controller.receiver_id}"
            )

            print(
                f"  Vehicle: "
                f"{controller.vehicle_name}"
            )

            # --------------------------------------------
            # Packet 1
            # --------------------------------------------

            print(
                "  Sending packet 1..."
            )

            if not controller.send_control(
                1000,
                2000
            ):

                print(
                    "  CONTROL SEND FAILED"
                )

                self.disconnect_all()

                return False

            time.sleep(
                0.1
            )

            # Drain immediate Nano response.
            while True:

                line = (
                    controller.read_line()
                )

                if not line:
                    break

                print(
                    f"  {line}"
                )

            # --------------------------------------------
            # Packet 2
            # --------------------------------------------

            print(
                "  Sending packet 2..."
            )

            if not controller.send_control(
                1100,
                2100
            ):

                print(
                    "  CONTROL SEND FAILED"
                )

                self.disconnect_all()

                return False

            time.sleep(
                0.2
            )

            # --------------------------------------------
            # Read Nano responses.
            # --------------------------------------------

            deadline = (
                time.time() + 1.0
            )

            while time.time() < deadline:

                line = (
                    controller.read_line()
                )

                if line:

                    print(
                        f"  {line}"
                    )

                else:

                    time.sleep(
                        0.01
                    )

        # ----------------------------------------------------
        # 9. Final status
        # ----------------------------------------------------

        print()

        self.print_status()

        print()

        self.print_active_vehicles()

        print()
        print(
            "RESULT: TEST_COMPLETE"
        )

        self.disconnect_all()

        return True


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":

    manager = RadioManager()

    manager.test()

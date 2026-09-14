import json
import os
import threading


REGISTRY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "vehicles.json"
)


class VehicleRegistry:
    """
    Persistent mapping between permanent RX IDs and
    DriveMatrix vehicle names.

    Example:

        9C52F7020F3C -> Car 1
        AABBCCDDEEFF -> Car 2

    RX ID is permanent.
    Vehicle name is assigned by the Pi.
    """

    def __init__(self, filename=REGISTRY_FILE):

        self.filename = filename
        self.lock = threading.Lock()

        self.vehicles = {}

        self.load()

    # ========================================================
    # LOAD
    # ========================================================

    def load(self):

        with self.lock:

            if not os.path.exists(self.filename):

                self.vehicles = {}

                return

            try:

                with open(
                    self.filename,
                    "r",
                    encoding="utf-8"
                ) as file:

                    data = json.load(file)

                if not isinstance(data, dict):

                    raise ValueError(
                        "Vehicle registry must be a JSON object"
                    )

                self.vehicles = data

            except Exception as e:

                print(
                    f"[VehicleRegistry] "
                    f"Failed to load registry: {e}"
                )

                self.vehicles = {}

    # ========================================================
    # SAVE
    # ========================================================

    def save(self):

        with self.lock:

            directory = os.path.dirname(
                self.filename
            )

            if directory:
                os.makedirs(
                    directory,
                    exist_ok=True
                )

            temp_file = (
                self.filename +
                ".tmp"
            )

            with open(
                temp_file,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    self.vehicles,
                    file,
                    indent=4,
                    sort_keys=True
                )

                file.write("\n")

            os.replace(
                temp_file,
                self.filename
            )

    # ========================================================
    # GET VEHICLE
    # ========================================================

    def get_vehicle(self, receiver_id):

        with self.lock:

            return self.vehicles.get(
                receiver_id
            )

    # ========================================================
    # HAS RECEIVER
    # ========================================================

    def has_receiver(self, receiver_id):

        with self.lock:

            return receiver_id in self.vehicles

    # ========================================================
    # ASSIGN NEXT NAME
    # ========================================================

    def assign_next(self, receiver_id):

        receiver_id = receiver_id.strip()

        if not receiver_id:

            raise ValueError(
                "receiver_id cannot be empty"
            )

        with self.lock:

            # Already assigned.
            if receiver_id in self.vehicles:

                return self.vehicles[
                    receiver_id
                ]

            used_numbers = []

            for vehicle in self.vehicles.values():

                name = str(vehicle.get("name", ""))

                if name.startswith("Car "):

                    number_text = name[4:].strip()

                    try:

                        number = int(
                            number_text
                        )

                        if number > 0:
                            used_numbers.append(
                                number
                            )

                    except ValueError:
                        pass

            next_number = 1

            while next_number in used_numbers:

                next_number += 1

            vehicle_name = (
                f"Car {next_number}"
            )

            self.vehicles[
                receiver_id
            ] = {
                "name": vehicle_name
            }

        self.save()

        print(
            f"[VehicleRegistry] Assigned "
            f"{receiver_id} -> {vehicle_name}"
        )

        return self.vehicles[
            receiver_id
        ]

    # ========================================================
    # SET NAME
    # ========================================================

    def set_name(
        self,
        receiver_id,
        vehicle_name
    ):

        receiver_id = receiver_id.strip()
        vehicle_name = vehicle_name.strip()

        if not receiver_id:

            raise ValueError(
                "receiver_id cannot be empty"
            )

        if not vehicle_name:

            raise ValueError(
                "vehicle_name cannot be empty"
            )

        with self.lock:

            self.vehicles[
                receiver_id
            ] = {
                "name": vehicle_name
            }

        self.save()

    # ========================================================
    # REMOVE
    # ========================================================

    def remove(self, receiver_id):

        with self.lock:

            if receiver_id not in self.vehicles:

                return False

            del self.vehicles[
                receiver_id
            ]

        self.save()

        return True

    # ========================================================
    # ALL VEHICLES
    # ========================================================

    def all(self):

        with self.lock:

            return dict(
                self.vehicles
            )

    # ========================================================
    # PRINT
    # ========================================================

    def print_all(self):

        print()
        print(
            "=============================="
        )

        print(
            "DriveMatrix Vehicle Registry"
        )

        print(
            "=============================="
        )

        if not self.vehicles:

            print(
                "No vehicles registered."
            )

            return

        print(
            f"Vehicles: {len(self.vehicles)}"
        )

        print()

        for receiver_id in sorted(
            self.vehicles
        ):

            vehicle = self.vehicles[
                receiver_id
            ]

            print(
                f"RX ID:  {receiver_id}"
            )

            print(
                f"  Name: "
                f"{vehicle.get('name', '')}"
            )

            print()


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    registry = VehicleRegistry()

    print(
        "Registry file:"
    )

    print(
        registry.filename
    )

    print()

    print(
        "Assigning test receiver..."
    )

    vehicle = registry.assign_next(
        "9C52F7020F3C"
    )

    print(
        f"Assigned name: "
        f"{vehicle['name']}"
    )

    registry.print_all()

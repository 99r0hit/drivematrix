import time
import random
import evdev
from g29_ffb import G29FFB

def run_random_ffb_test():
    print("🔍 Searching for FFB-capable steering wheel device...")
    try:
        ffb = G29FFB()
    except RuntimeError as e:
        print(f"❌ {e}")
        return

    print(f"✅ Connected to wheel on {ffb.dev.path} ({ffb.dev.name})")
    ffb.disable_autocenter()

    print("🚀 Starting random FFB loop. Press Ctrl+C to stop.")
    try:
        while True:
            # Pick a random stiffness percentage between 0% and 100%
            random_stiffness = random.uniform(0.0, 100.0)
            
            print(f"⚡ Applying Auto-Center Spring Stiffness: {random_stiffness:.1f}%")
            ffb.set_hardware_autocenter(random_stiffness)
            
            # Wait between 3 to 5 seconds before changing force
            sleep_duration = random.uniform(3.0, 5.0)
            time.sleep(sleep_duration)

    except KeyboardInterrupt:
        print("\n🛑 Stopping test and clearing force...")
        ffb.stop()
        print("✅ Done.")

if __name__ == "__main__":
    run_random_ffb_test()

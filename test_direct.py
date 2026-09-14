# test_direct.py
import evdev
import time
from g29_ffb import G29FFB

dev = evdev.InputDevice('/dev/input/event0')
print(f"Connecting to: {dev.name}")

ffb = G29FFB(dev)
ffb.disable_autocenter()

print("Pulling Left...")
ffb.set_force(-0.7)
time.sleep(2)

print("Pulling Right...")
ffb.set_force(0.7)
time.sleep(2)

ffb.stop()
print("Done.")


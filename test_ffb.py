import time
from ffb_manager import FFBManager

print("Initializing FFB Manager...", flush=True)
ffb = FFBManager()

if not ffb.device:
    print("❌ No FFB device found!", flush=True)
    exit()

print("\n--- Test 1: Baseline Centering (50%) ---", flush=True)
ffb.set_autocenter_strength(50)
print("Hold the wheel slightly off-center now...", flush=True)
time.sleep(2)

print("\n--- Test 2: Trigger Hardware Bump Pulses ---", flush=True)
for i in range(3):
    print(f"💥 Bump {i+1}!", flush=True)
    ffb.trigger_hardware_bump(pulse_strength=100, duration_sec=0.1)
    time.sleep(0.3)

print("\n✅ FFB Test Complete!", flush=True)

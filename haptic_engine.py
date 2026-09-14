import math
import time

class HapticEngine:
    def __init__(self, imu_reader):
        self.imu = imu_reader
        
        # --- GAINS & TUNING PARAMETERS ---
        self.grip_threshold_g = 0.65    # Max lateral G before tire slip (0.3 = loose, 0.8 = high grip)
        self.bump_sensitivity = 2.0     # Road vibration multiplier
        self.centering_gain = 25.0      # Weight added to steering resistance per G (%)
        self.rumble_gain = 15.0         # Vibration boost from vertical spikes (%)
        self.alpha = 0.2                # Low-pass filter smoothing (0.05 = heavy smooth, 0.5 = raw)

        # Static offset calibration for sensor tilt
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.offset_z = 0.0

        # Filter states
        self.smooth_lat_g = 0.0
        self.smooth_long_g = 0.0

    def calibrate_resting_offsets(self, samples=50):
        """Zero out sensor mounting tilt while stationary on flat ground."""
        sum_x, sum_y, sum_z = 0.0, 0.0, 0.0
        valid_samples = 0
        
        for _ in range(samples):
            sum_x += self.imu.accel_x
            sum_y += self.imu.accel_y
            sum_z += self.imu.accel_z
            valid_samples += 1
            time.sleep(0.01)

        if valid_samples > 0:
            self.offset_x = sum_x / valid_samples
            self.offset_y = sum_y / valid_samples
            # Gravity offset (keep 1.0G on vertical axis)
            self.offset_z = (sum_z / valid_samples) - 1.0
            print(f"✅ [Haptics] Calibrated Offsets -> X: {self.offset_x:+.2f}, Y: {self.offset_y:+.2f}, Z: {self.offset_z:+.2f}")

    def update_tuning(self, grip_thresh=None, bump_sens=None, centering_gain=None, alpha=None):
        """Thread-safe parameter updates for live Web UI tuning."""
        if grip_thresh is not None: self.grip_threshold_g = float(grip_thresh)
        if bump_sens is not None: self.bump_sensitivity = float(bump_sens)
        if centering_gain is not None: self.centering_gain = float(centering_gain)
        if alpha is not None: self.alpha = float(alpha)

    def calculate_effects(self):
        # 1. Read & apply calibration offsets
        ax = self.imu.accel_x - self.offset_x
        ay = self.imu.accel_y - self.offset_y
        az = self.imu.accel_z - self.offset_z

        # 2. Smooth cornering & longitudinal Gs using Exponential Moving Average
        self.smooth_lat_g += self.alpha * (ay - self.smooth_lat_g)
        self.smooth_long_g += self.alpha * (ax - self.smooth_long_g)

        # 3. Calculate Steering Load / Self-Centering Force
        cornering_load = min(abs(self.smooth_lat_g) / 1.2, 1.0)

        # 4. Calculate Road Bump/Rumble
        vertical_jitter = abs(az - 1.0)
        rumble_intensity = min(vertical_jitter * self.bump_sensitivity, 1.0)

        # 5. Calculate Traction Loss (Slip)
        total_lat_demand = abs(self.smooth_lat_g)
        traction_loss = 0.0
        if total_lat_demand > self.grip_threshold_g:
            slip = total_lat_demand - self.grip_threshold_g
            traction_loss = min(slip / 0.4, 1.0)

        return {
            "centering_force": round(cornering_load, 3),
            "rumble_intensity": round(rumble_intensity, 3),
            "traction_loss": round(traction_loss, 3),
            "weight_transfer": round(self.smooth_long_g, 3),
            "lat_g": round(self.smooth_lat_g, 2),
            "long_g": round(self.smooth_long_g, 2),
            "haptic_boost": (cornering_load * self.centering_gain) + (rumble_intensity * self.rumble_gain)
        }

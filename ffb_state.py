import time

class VehicleState:
    def __init__(self):
        self.steering_angle = 0.0    # -1.0 to +1.0
        self.throttle = 0.0          # 0.0 to 1.0
        self.brake = 0.0             # 0.0 to 1.0
        self.estimated_speed = 0.0   # 0.0 to 1.0
        self.accel_x = 0.0
        self.accel_y = 0.0
        self.accel_z = 0.0
        self.last_update_time = time.time()

    def update_imu(self, ax, ay, az):
        self.accel_x = ax
        self.accel_y = ay
        self.accel_z = az

    def update_inputs(self, raw_steer, throttle, brake):
        now = time.time()
        dt = min(0.05, max(0.001, now - self.last_update_time))
        self.last_update_time = now

        # Normalize inputs
        self.steering_angle = max(-1.0, min(1.0, raw_steer / 1000.0))
        self.throttle = max(0.0, min(1.0, throttle))
        self.brake = max(0.0, min(1.0, brake))

        # Target speed driven directly by pedal position
        if self.brake > 0.05:
            target_speed = 0.0
            response_rate = 8.0 * self.brake  # Immediate drop on brake
        elif self.throttle > 0.05:
            target_speed = self.throttle
            response_rate = 3.5 * self.throttle  # Rapid response on throttle
        else:
            target_speed = 0.0
            response_rate = 5.0  # Instant drop to zero when coasting

        # Smooth exponential interpolation without integration lag
        alpha = min(1.0, response_rate * dt)
        self.estimated_speed += (target_speed - self.estimated_speed) * alpha
        
        if self.estimated_speed < 0.01:
            self.estimated_speed = 0.0

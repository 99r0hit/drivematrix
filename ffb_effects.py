import math

class FFBEffectsEngine:
    def __init__(self):
        self.impact_gain = 2.5  # High gain multiplier for flick sensitivity

    def compute_autocenter(self, state):
        # DISABLED: Returning 0 to isolate IMU haptic feedback
        return 0.0

    def compute_sat(self, state):
        # DISABLED: Returning 0 to isolate IMU haptic feedback
        return 0.0

    def compute_impact_kick(self, state):
        """
        Only returns a kick value if acceleration exceeds a realistic bump threshold.
        """
        # Example check based on your IMU acceleration vectors
        # Adjust 'threshold' higher if it's too sensitive
        threshold = 1.5  
        
        # Get current impact magnitude (using your state's accel data)
        current_accel = getattr(state, 'impact_magnitude', 0.0)
        
        if current_accel > threshold:
            # Scale the kick based on how hard the hit was
            return min(100.0, (current_accel - threshold) * 20.0)
        
        return 0.0 # Returns 0 when driving normally, keeping the loop quiet!

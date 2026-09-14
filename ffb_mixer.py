class FFBMixer:
    def __init__(self):
        self.master_gain = 1.0

    def mix_all_effects(self, autocenter_pct, sat_mult, kick_boost, is_autocenter_on):
        """Combines Auto-Center spring, SAT weight, and transient impact kicks."""
        if not is_autocenter_on:
            return 0.0

        # 1. Base Auto-Center spring scaled by speed SAT multiplier
        base_stiffness = autocenter_pct * sat_mult

        # 2. Add impact impulse on top of current steering resistance
        total_stiffness = (base_stiffness + kick_boost) * self.master_gain

        return max(0.0, min(100.0, total_stiffness))

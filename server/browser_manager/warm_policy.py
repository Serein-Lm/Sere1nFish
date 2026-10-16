"""Bounded idle reserve based on recent declared browser demand."""
import time
from collections import deque


class AdaptiveWarmPool:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.samples = deque(maxlen=2048)

    def target(self, config, *, demand: int) -> int:
        ceiling = min(config.warm_pool_size, config.max_containers)
        if not config.adaptive_warm_pool:
            return ceiling
        now = self.clock()
        cutoff = now - config.warm_demand_window_seconds
        while self.samples and self.samples[0][0] <= cutoff:
            self.samples.popleft()
        # One peak per second bounds memory even for a high request rate.
        if self.samples and int(self.samples[-1][0]) == int(now):
            self.samples[-1] = (now, max(self.samples[-1][1], max(0, demand)))
        else:
            self.samples.append((now, max(0, demand)))
        peak = max((count for _, count in self.samples), default=0)
        return min(ceiling, max(config.warm_pool_min_size, peak))

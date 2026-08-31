from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DetectionGap:
    """Time interval where one actor should be treated as not detected."""

    vehicle_name: str
    start_time_s: float
    end_time_s: float

    def contains(self, time_s: float) -> bool:
        """Return whether ``time_s`` lies inside the closed gap interval."""
        return self.start_time_s <= time_s <= self.end_time_s

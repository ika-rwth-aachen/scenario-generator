from __future__ import annotations

from scenario_generator.metrics.metric import Metric
from scenario_generator.metrics.metric_utils import EPSILON, overlap_time_for_axes
from scenario_generator.scenario_elements.road_user.road_user import ActorState


class THWMetric(Metric):
    """Time-headway metric for a moving source actor and a target actor."""

    name = "THW"
    unit = "s"
    directed = True

    def pairwise(self, moving: ActorState, static: ActorState) -> float | None:
        """Return the time until ``moving`` reaches ``static``'s current box."""
        if moving.speed_mps <= EPSILON:
            return None
        return overlap_time_for_axes(
            moving,
            static,
            relative_velocity=(-moving.vx_mps, -moving.vy_mps),
        )


_thw_metric = THWMetric()


def pairwise_thw(moving: ActorState, static: ActorState) -> float | None:
    """Compatibility wrapper for callers that use the function API."""
    return _thw_metric.pairwise(moving, static)


def min_thw_targets_by_actor(
    states: dict[str, ActorState],
) -> dict[str, tuple[str, float] | None]:
    """Return each source actor's closest THW target and value."""
    return _thw_metric.targets_by_actor(states)


def format_thw(thw_s: float | None) -> str:
    """Format THW for display in the canvas overlay."""
    return _thw_metric.format(thw_s)

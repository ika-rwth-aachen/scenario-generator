from __future__ import annotations

from scenario_generator.metrics.metric import Metric
from scenario_generator.metrics.metric_utils import overlap_time_for_axes
from scenario_generator.scenario_elements.road_user.road_user import ActorState


class TTCMetric(Metric):
    """Time-to-collision metric using swept oriented bounding boxes."""

    name = "TTC"
    unit = "s"
    directed = False

    def pairwise(self, first: ActorState, second: ActorState) -> float | None:
        """Return the first future overlap time for two moving actors."""
        return overlap_time_for_axes(first, second)


_ttc_metric = TTCMetric()


def pairwise_ttc(first: ActorState, second: ActorState) -> float | None:
    """Compatibility wrapper for callers that use the function API."""
    return _ttc_metric.pairwise(first, second)


def min_ttc(states: dict[str, ActorState]) -> float | None:
    """Return the smallest TTC across all actor pairs."""
    return _ttc_metric.minimum(states)


def min_ttc_by_actor(states: dict[str, ActorState]) -> dict[str, float | None]:
    """Return each actor's smallest TTC value."""
    return _ttc_metric.by_actor(states)


def min_ttc_targets_by_actor(
    states: dict[str, ActorState],
) -> dict[str, tuple[str, float] | None]:
    """Return each actor's TTC target and value."""
    return _ttc_metric.targets_by_actor(states)


def format_ttc(ttc_s: float | None) -> str:
    """Format TTC for display in the canvas overlay."""
    return _ttc_metric.format(ttc_s)

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from scenario_generator.scenario_elements.road_user.road_user import ActorState


class Metric(ABC):
    """Template for pairwise scenario metrics."""

    name: str
    unit: str
    directed: bool

    @abstractmethod
    def pairwise(self, first: ActorState, second: ActorState) -> float | None:
        """Return this metric for a pair of actors, or ``None`` if not applicable."""
        raise NotImplementedError

    def minimum(self, states: dict[str, ActorState]) -> float | None:
        """Return the smallest metric value over all actor pairs."""
        minimum: float | None = None
        for _source_name, _target_name, value in self.iter_values(states):
            if value is None:
                continue
            minimum = value if minimum is None else min(minimum, value)
        return minimum

    def by_actor(self, states: dict[str, ActorState]) -> dict[str, float | None]:
        """Return each actor's best metric value without exposing the target name."""
        result: dict[str, float | None] = dict.fromkeys(states)
        for source_name, target_name, value in self.iter_values(states):
            if value is None:
                continue
            result[source_name] = (
                value
                if result[source_name] is None
                else min(result[source_name], value)
            )
            if not self.directed:
                result[target_name] = (
                    value
                    if result[target_name] is None
                    else min(result[target_name], value)
                )
        return result

    def targets_by_actor(
        self,
        states: dict[str, ActorState],
    ) -> dict[str, tuple[str, float] | None]:
        """Return each actor's nearest/most relevant target and metric value."""
        result: dict[str, tuple[str, float] | None] = dict.fromkeys(states)
        for source_name, target_name, value in self.iter_values(states):
            if value is None:
                continue
            current = result[source_name]
            if current is None or value < current[1]:
                result[source_name] = (target_name, value)
            if not self.directed:
                current = result[target_name]
                if current is None or value < current[1]:
                    result[target_name] = (source_name, value)
        return result

    def iter_values(
        self,
        states: dict[str, ActorState],
    ) -> Iterator[tuple[str, str, float | None]]:
        """Yield all pairwise metric values with source and target names."""
        items = list(states.items())
        if self.directed:
            for source_name, source_state in items:
                for target_name, target_state in items:
                    if source_name == target_name:
                        continue
                    yield (
                        source_name,
                        target_name,
                        self.pairwise(source_state, target_state),
                    )
            return
        for first_index, (first_name, first_state) in enumerate(items):
            for second_name, second_state in items[first_index + 1 :]:
                yield first_name, second_name, self.pairwise(first_state, second_state)

    def format(self, value: float | None) -> str:
        """Format a metric value for compact GUI labels."""
        if value is None:
            return f"{self.name}: --"
        return f"{self.name}: {value:.2f} {self.unit}"

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from scenario_generator.config.settings import load_profile_distance_samples_per_segment
from scenario_generator.geometry_utils import (
    CURVE_SAMPLE_PERIOD_S,
    MIN_CURVE_SAMPLES_PER_SEGMENT,
    catmull_rom,
    heading_between,
    normalize_angle_rad,
    smoothstep,
)


@dataclass
class Waypoint:
    """Timestamped 2D control point for an actor trajectory."""

    time_s: float
    x_m: float
    y_m: float
    speed_mps: float | None = None


@dataclass
class Trajectory:
    waypoints: list[Waypoint] = field(default_factory=list)
    detected: list[bool] | None = None
    parked_yaw_rad: float = 0.0

    PROFILE_DISTANCE_SAMPLES_PER_SEGMENT = load_profile_distance_samples_per_segment()
    MIN_DISTANCE_M = 1e-9
    MIN_SPEED_SUM_MPS = 1e-9

    def as_series(self) -> dict[str, list[float] | list[bool]]:
        if len(self.waypoints) == 1:
            trajectory = self.as_parked_series()
        else:
            trajectory = self.as_moving_series()
        if self.detected is not None:
            trajectory["detected"] = self.detected
        return trajectory

    def sorted_waypoints(self) -> list[Waypoint]:
        return sorted(self.waypoints, key=lambda waypoint: waypoint.time_s)

    def validate_moving_waypoints(self, waypoints: list[Waypoint]) -> None:
        if len(waypoints) < 2:
            raise ValueError("Each vehicle needs at least two waypoints.")
        for previous, current in itertools.pairwise(waypoints):
            if current.time_s <= previous.time_s:
                raise ValueError("Waypoint times must be unique and increasing.")

    def curve_point_for_segment(
        self,
        segment_index: int,
        fraction: float,
    ) -> tuple[float, float]:
        waypoints = self.sorted_waypoints()
        last_index = len(waypoints) - 1
        p0 = waypoints[max(segment_index - 1, 0)]
        p1 = waypoints[segment_index]
        p2 = waypoints[segment_index + 1]
        p3 = waypoints[min(segment_index + 2, last_index)]
        return catmull_rom(
            (p0.x_m, p0.y_m),
            (p1.x_m, p1.y_m),
            (p2.x_m, p2.y_m),
            (p3.x_m, p3.y_m),
            fraction,
        )

    @staticmethod
    def sample_count_for_segment(start_time: float, end_time: float) -> int:
        duration = end_time - start_time
        if duration <= 0.0:
            raise ValueError("Waypoint times must be unique and increasing.")
        return max(
            MIN_CURVE_SAMPLES_PER_SEGMENT,
            math.ceil(duration / CURVE_SAMPLE_PERIOD_S),
        )

    def sampled_curve_points_for_segment(
        self,
        segment_index: int,
        include_end: bool = True,
    ) -> list[tuple[float, float, float]]:
        waypoints = self.sorted_waypoints()
        start = waypoints[segment_index]
        end = waypoints[segment_index + 1]
        sample_count = self.sample_count_for_segment(start.time_s, end.time_s)
        return self.sampled_curve_points_for_segment_count(
            segment_index, sample_count, include_end
        )

    def sampled_curve_points_for_segment_count(
        self,
        segment_index: int,
        sample_count: int,
        include_end: bool = True,
    ) -> list[tuple[float, float, float]]:
        """Sample one curve segment with an explicit, time-independent count."""
        waypoints = self.sorted_waypoints()
        start = waypoints[segment_index]
        end = waypoints[segment_index + 1]
        sample_count = max(int(sample_count), 1)
        stop = sample_count + 1 if include_end else sample_count
        points: list[tuple[float, float, float]] = []
        for sample_index in range(stop):
            fraction = sample_index / sample_count
            x_m, y_m = self.curve_point_for_segment(segment_index, fraction)
            time_s = start.time_s + (end.time_s - start.time_s) * fraction
            points.append((time_s, x_m, y_m))
        return points

    def sampled_curve_points(self) -> list[tuple[float, float, float]]:
        waypoints = self.sorted_waypoints()
        points: list[tuple[float, float, float]] = []
        for index in range(len(waypoints) - 1):
            points.extend(
                self.sampled_curve_points_for_segment(
                    index,
                    include_end=index == len(waypoints) - 2,
                ),
            )
        return points

    def segment_curve_distance(self, segment_index: int) -> float:
        points = self.sampled_curve_points_for_segment(segment_index)
        return self.curve_distance_from_points(points)

    def profile_segment_curve_distance(self, segment_index: int) -> float:
        """Return a stable geometric length used to derive profile timestamps."""
        points = self.sampled_curve_points_for_segment_count(
            segment_index,
            self.PROFILE_DISTANCE_SAMPLES_PER_SEGMENT,
        )
        return self.curve_distance_from_points(points)

    @staticmethod
    def curve_distance_from_points(
        points: list[tuple[float, float, float]],
    ) -> float:
        distance = 0.0
        for previous, current in itertools.pairwise(points):
            distance += math.hypot(current[1] - previous[1], current[2] - previous[2])
        return distance

    def cumulative_waypoint_distances(self) -> list[float]:
        """Return cumulative geometric distances at the sorted support points."""
        waypoints = self.sorted_waypoints()
        if not waypoints:
            return []
        distances = [0.0]
        for segment_index in range(len(waypoints) - 1):
            distances.append(
                distances[-1] + self.profile_segment_curve_distance(segment_index)
            )
        return distances

    def segment_average_speeds(self) -> list[float]:
        waypoints = self.sorted_waypoints()
        averages: list[float] = []
        for segment_index, (start, end) in enumerate(itertools.pairwise(waypoints)):
            duration = end.time_s - start.time_s
            if duration <= 0.0:
                raise ValueError("Waypoint times must be unique and increasing.")
            averages.append(
                self.profile_segment_curve_distance(segment_index) / duration
            )
        return averages

    @staticmethod
    def smooth_waypoint_speeds(segment_averages: list[float]) -> list[float]:
        if not segment_averages:
            return []
        targets = [segment_averages[0]]
        for previous, current in itertools.pairwise(segment_averages):
            targets.append((previous + current) / 2.0)
        targets.append(segment_averages[-1])
        signs = [1.0]
        offsets = [0.0]
        for average in segment_averages:
            signs.append(-signs[-1])
            offsets.append(2.0 * average - offsets[-1])
        preferred_start_speed = sum(
            sign * (target - offset)
            for sign, offset, target in zip(signs, offsets, targets)
        ) / len(targets)
        lower_bound = -math.inf
        upper_bound = math.inf
        for sign, offset in zip(signs, offsets):
            if sign > 0.0:
                lower_bound = max(lower_bound, -offset)
            else:
                upper_bound = min(upper_bound, offset)
        if lower_bound <= upper_bound:
            start_speed = min(max(preferred_start_speed, lower_bound), upper_bound)
        else:
            start_speed = max(0.0, preferred_start_speed)
        return [
            max(0.0, sign * start_speed + offset)
            for sign, offset in zip(signs, offsets)
        ]

    @staticmethod
    def anchored_waypoint_speeds(
        segment_averages: list[float],
        initial_speed_mps: float,
    ) -> tuple[list[float], bool]:
        """Resolve nonnegative support speeds with an exact initial value.

        Each following value locally preserves the corresponding segment
        average. If that would require a negative speed, it is clamped to zero
        and the returned flag tells the GUI that an approximation was needed.
        """
        initial_speed = max(0.0, float(initial_speed_mps))
        speeds = [initial_speed]
        adjusted = initial_speed != float(initial_speed_mps)
        for average in segment_averages:
            next_speed = 2.0 * average - speeds[-1]
            if next_speed < 0.0:
                next_speed = 0.0
                adjusted = True
            speeds.append(next_speed)
        return speeds, adjusted

    def initial_speed_mps(self) -> float:
        """Return the configured initial speed, defaulting to zero."""
        waypoints = self.sorted_waypoints()
        if not waypoints or waypoints[0].speed_mps is None:
            return 0.0
        return max(0.0, float(waypoints[0].speed_mps))

    def resolved_waypoint_speeds(self) -> tuple[list[float], bool]:
        """Return explicit support speeds or derive them for legacy input."""
        waypoints = self.sorted_waypoints()
        if all(waypoint.speed_mps is not None for waypoint in waypoints):
            speeds: list[float] = []
            for waypoint in waypoints:
                assert waypoint.speed_mps is not None
                speed = float(waypoint.speed_mps)
                if speed < 0.0:
                    raise ValueError("Waypoint speeds must be nonnegative.")
                speeds.append(speed)
            return speeds, False
        return self.anchored_waypoint_speeds(
            self.segment_average_speeds(),
            self.initial_speed_mps(),
        )

    def synchronize_times_from_speeds(self, start_time_s: float = 0.0) -> None:
        """Derive times so every segment matches its endpoint-speed average."""
        waypoints = self.sorted_waypoints()
        self.validate_explicit_speeds(waypoints)
        segment_distances = [
            self.profile_segment_curve_distance(segment_index)
            for segment_index in range(len(waypoints) - 1)
        ]
        current_time = max(0.0, float(start_time_s))
        waypoints[0].time_s = current_time
        for distance, start, end in zip(
            segment_distances, waypoints, waypoints[1:]
        ):
            speed_sum = float(start.speed_mps) + float(end.speed_mps)
            if distance <= self.MIN_DISTANCE_M:
                raise ValueError(
                    "Consecutive trajectory points must not share a position."
                )
            if speed_sum <= self.MIN_SPEED_SUM_MPS:
                raise ValueError(
                    "A moving segment cannot have zero speed at both endpoints."
                )
            current_time += 2.0 * distance / speed_sum
            end.time_s = current_time
        self.waypoints.sort(key=lambda waypoint: waypoint.time_s)

    @staticmethod
    def validate_explicit_speeds(waypoints: list[Waypoint]) -> None:
        if len(waypoints) < 2:
            return
        for waypoint in waypoints:
            if waypoint.speed_mps is None:
                raise ValueError("Each trajectory point needs a speed.")
            if float(waypoint.speed_mps) < 0.0:
                raise ValueError("Trajectory point speeds must be nonnegative.")

    def synchronize_speed_profile_times(self, start_time_s: float = 0.0) -> None:
        """Compatibility alias for configurations created by older releases."""
        self.synchronize_times_from_speeds(start_time_s)

    @staticmethod
    def smooth_speed_at_fraction(start_speed: float, end_speed: float, fraction: float) -> float:
        return start_speed + (end_speed - start_speed) * smoothstep(fraction)

    def as_moving_series(self) -> dict[str, list[float] | list[bool]]:
        waypoints = self.sorted_waypoints()
        self.validate_moving_waypoints(waypoints)
        waypoint_speeds, _adjusted = self.resolved_waypoint_speeds()
        sampled_points: list[tuple[float, float, float]] = []
        speed_values: list[float] = []
        for segment_index in range(len(waypoints) - 1):
            segment_points = self.sampled_curve_points_for_segment(
                segment_index,
                include_end=segment_index == len(waypoints) - 2,
            )
            start_time = waypoints[segment_index].time_s
            end_time = waypoints[segment_index + 1].time_s
            duration = end_time - start_time
            for time_s, x_m, y_m in segment_points:
                fraction = 0.0 if duration <= 0.0 else (time_s - start_time) / duration
                sampled_points.append((time_s, x_m, y_m))
                speed_values.append(
                    self.smooth_speed_at_fraction(
                        waypoint_speeds[segment_index],
                        waypoint_speeds[segment_index + 1],
                        fraction,
                    ),
                )
        x_values = [point[1] for point in sampled_points]
        y_values = [point[2] for point in sampled_points]
        time_values = [point[0] for point in sampled_points]
        yaw_values: list[float] = []
        last_yaw = 0.0
        for index, _time_s in enumerate(time_values):
            last_yaw = heading_between(x_values, y_values, index, last_yaw)
            yaw_values.append(last_yaw)
        trajectory: dict[str, list[float] | list[bool]] = {
            "time_s": time_values,
            "x_m": x_values,
            "y_m": y_values,
            "speed_mps": speed_values,
            "yaw_rad": yaw_values,
        }
        if self.detected is not None:
            trajectory["detected"] = self.detected
        return trajectory

    def as_parked_series(self) -> dict[str, list[float]]:
        if not self.waypoints:
            raise ValueError("A parked vehicle needs at least one waypoint.")
        first = self.sorted_waypoints()[0]
        return {
            "time_s": [float(first.time_s)],
            "x_m": [float(first.x_m)],
            "y_m": [float(first.y_m)],
            "speed_mps": [0.0],
            "yaw_rad": [float(self.parked_yaw_rad)],
        }

    def parked_yaw(self, fallback_yaw_rad: float = 0.0) -> float:
        waypoints = self.sorted_waypoints()
        if len(waypoints) >= 2:
            first = waypoints[0]
            second = waypoints[1]
            dx = second.x_m - first.x_m
            dy = second.y_m - first.y_m
            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                return math.atan2(dy, dx)
        return fallback_yaw_rad

    def interpolate(self, interpol_time: float) -> tuple[float, float, float]:
        if not self.waypoints:
            raise ValueError("Trajectory is empty.")
        if len(self.waypoints) == 1:
            parked = self.as_parked_series()
            return parked["x_m"][0], parked["y_m"][0], parked["yaw_rad"][0]
        return interpolate_trajectory(self.as_moving_series(), interpol_time)


def interpolate_trajectory(trajectory: dict[str, list[float]], time_s: float) -> tuple[float, float, float]:
	"""Interpolate one sampled trajectory series at a given time."""
	times = trajectory["time_s"]
	xs = trajectory["x_m"]
	ys = trajectory["y_m"]
	yaws = trajectory["yaw_rad"]
	if not times:
		raise ValueError("Trajectory is empty.")
	if time_s <= times[0]:
		return xs[0], ys[0], yaws[0]
	if time_s >= times[-1]:
		return xs[-1], ys[-1], yaws[-1]
	for index in range(1, len(times)):
		if times[index] < time_s:
			continue
		left = index - 1
		span = times[index] - times[left]
		fraction = 0.0 if span == 0.0 else (time_s - times[left]) / span
		x = xs[left] + (xs[index] - xs[left]) * fraction
		y = ys[left] + (ys[index] - ys[left]) * fraction
		yaw_delta = normalize_angle_rad(yaws[index] - yaws[left])
		yaw = normalize_angle_rad(yaws[left] + yaw_delta * fraction)
		return x, y, yaw
	return xs[-1], ys[-1], yaws[-1]

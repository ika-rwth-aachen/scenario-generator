from __future__ import annotations

import itertools
import math
from bisect import bisect_right
from dataclasses import dataclass, field

from scenario_generator.config.settings import load_profile_distance_samples_per_segment
from scenario_generator.geometry_utils import (
    CURVE_SAMPLE_PERIOD_S,
    MIN_CURVE_SAMPLES_PER_SEGMENT,
    catmull_rom,
    heading_between,
    normalize_angle_rad,
)

# These modes select the trajectory's time parameterization. The spatial
# (x, y) path remains a Catmull-Rom curve for every mode.
TRAJECTORY_INTERPOLATION_MODES = (
    "hermite", "linear_velocity", "catmull_rom",
)
DEFAULT_TRAJECTORY_INTERPOLATION = "hermite"


INTERPOLATION_MODE_ALIASES = {
    # Name used by main before the Hermite mode was simplified.
    "cubic_hermite": "hermite",
}


def validate_interpolation_mode(value: str) -> str:
    value = INTERPOLATION_MODE_ALIASES.get(value, value)
    if value not in TRAJECTORY_INTERPOLATION_MODES:
        raise ValueError(f"Unsupported trajectory interpolation: {value}")
    return value


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
    interpolation_mode: str = DEFAULT_TRAJECTORY_INTERPOLATION

    PROFILE_DISTANCE_SAMPLES_PER_SEGMENT = load_profile_distance_samples_per_segment()
    MIN_DISTANCE_M = 1e-9
    MIN_SPEED_SUM_MPS = 1e-9

    def __post_init__(self) -> None:
        self.interpolation_mode = validate_interpolation_mode(
            self.interpolation_mode
        )

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
    def hermite_distance_speed(length: float, duration: float, start_speed: float,
                               end_speed: float, fraction: float) -> tuple[float, float]:
        """Minimum squared acceleration with fixed distance, time and speeds.

        Cubic Hermite distance gives a quadratic velocity. If it would reverse,
        the nonnegative minimum consists of two quadratic ramps and a stop.
        Endpoint accelerations are free, rather than forced to zero.
        """
        if length <= Trajectory.MIN_DISTANCE_M:
            return 0.0, 0.0
        mean = length / duration
        linear = 6 * mean - 4 * start_speed - 2 * end_speed
        quadratic = 3 * (start_speed + end_speed) - 6 * mean
        vertex = -linear / (2 * quadratic) if quadratic > 0 else -1.0
        if 0 < vertex < 1 and start_speed + linear * vertex + quadratic * vertex ** 2 < 0:
            scale = 3 * length / (start_speed ** 1.5 + end_speed ** 1.5)
            left_duration = scale * math.sqrt(start_speed)
            right_duration = scale * math.sqrt(end_speed)
            time = fraction * duration
            left_distance = start_speed * left_duration / 3
            if left_duration > 0 and time < left_duration:
                remaining = 1 - time / left_duration
                return left_distance * (1 - remaining ** 3), start_speed * remaining ** 2
            if right_duration > 0 and time > duration - right_duration:
                progress = (time - duration + right_duration) / right_duration
                return (left_distance + end_speed * right_duration * progress ** 3 / 3,
                        end_speed * progress ** 2)
            return left_distance, 0.0
        distance = duration * (start_speed * fraction + linear * fraction ** 2 / 2
                               + quadratic * fraction ** 3 / 3)
        speed = start_speed + linear * fraction + quadratic * fraction ** 2
        return distance, max(0.0, speed)

    @staticmethod
    def continuous_velocity_coefficients(length, duration, v0, v1, a0, a1):
        """Quartic velocity: fixed integral and shared endpoint accelerations."""
        mean = length / duration
        return [v0, duration * a0,
                30 * mean - 18 * v0 - 12 * v1 - 4.5 * duration * a0 + 1.5 * duration * a1,
                -60 * mean + 32 * v0 + 28 * v1 + 6 * duration * a0 - 4 * duration * a1,
                30 * mean - 15 * v0 - 15 * v1 - 2.5 * duration * a0 + 2.5 * duration * a1]

    @staticmethod
    def polynomial_value(coefficients, fraction):
        value = 0.0
        for coefficient in reversed(coefficients):
            value = value * fraction + coefficient
        return value

    @classmethod
    def polynomial_minimum(cls, coefficients):
        """Find extrema on [0, 1] by recursively isolating derivative roots."""
        def roots(poly):
            while len(poly) > 1 and abs(poly[-1]) < 1e-12:
                poly = poly[:-1]
            if len(poly) <= 1:
                return []
            derivative = [i * poly[i] for i in range(1, len(poly))]
            boundaries = [0.0, *roots(derivative), 1.0]
            result = []
            for left, right in itertools.pairwise(boundaries):
                lo = cls.polynomial_value(poly, left)
                hi = cls.polynomial_value(poly, right)
                if abs(lo) < 1e-10:
                    result.append(left)
                if lo * hi < 0:
                    for _ in range(45):
                        middle = (left + right) / 2
                        value = cls.polynomial_value(poly, middle)
                        if lo * value <= 0:
                            right = middle
                        else:
                            left, lo = middle, value
                    result.append((left + right) / 2)
            return result
        derivative = [i * coefficients[i] for i in range(1, len(coefficients))]
        return min(cls.polynomial_value(coefficients, u) for u in [0, 1, *roots(derivative)])

    @classmethod
    def continuous_hermite_profiles(cls, lengths, durations, speeds):
        """Minimize integrated squared acceleration with C1 velocity at knots.

        Exact segment distances require quartic velocity (quintic distance)
        when both endpoint accelerations are shared. No endpoint slope is fixed.
        For infeasible negative-speed polynomials, use nonnegative smooth ramps
        and constrain their adjoining accelerations to zero as well.
        """
        count = len(speeds)
        diagonal = [0.0] * count
        upper = [0.0] * (count - 1)
        rhs = [0.0] * count
        def inner(a, b, duration):
            return sum(i * j * a[i] * b[j] / (i + j - 1) / duration
                       for i in range(1, 5) for j in range(1, 5))
        for i, (length, duration) in enumerate(zip(lengths, durations)):
            base = cls.continuous_velocity_coefficients(length, duration, speeds[i], speeds[i + 1], 0, 0)
            left = [0, duration, -4.5 * duration, 6 * duration, -2.5 * duration]
            right = [0, 0, 1.5 * duration, -4 * duration, 2.5 * duration]
            diagonal[i] += inner(left, left, duration)
            diagonal[i + 1] += inner(right, right, duration)
            upper[i] = inner(left, right, duration)
            rhs[i] -= inner(left, base, duration)
            rhs[i + 1] -= inner(right, base, duration)
        fallback = set()
        while True:
            fixed = {j for i in fallback for j in (i, i + 1)}
            # A nonnegative differentiable speed at an interior stop has slope 0.
            fixed.update(i for i in range(1, count - 1) if speeds[i] == 0)
            diag, off, values = diagonal[:], upper[:], rhs[:]
            for i in fixed:
                diag[i], values[i] = 1.0, 0.0
                if i:
                    off[i - 1] = 0.0
                if i < count - 1:
                    off[i] = 0.0
            for i in range(1, count):
                factor = off[i - 1] / diag[i - 1]
                diag[i] -= factor * off[i - 1]
                values[i] -= factor * values[i - 1]
            slopes = [0.0] * count
            slopes[-1] = values[-1] / diag[-1]
            for i in range(count - 2, -1, -1):
                slopes[i] = (values[i] - off[i] * slopes[i + 1]) / diag[i]
            profiles = [cls.continuous_velocity_coefficients(length, duration, speeds[i], speeds[i + 1], slopes[i], slopes[i + 1])
                        for i, (length, duration) in enumerate(zip(lengths, durations))]
            negative = {i for i, profile in enumerate(profiles)
                        if i not in fallback and cls.polynomial_minimum(profile) < -1e-8}
            if not negative:
                return [None if i in fallback else profile for i, profile in enumerate(profiles)]
            fallback.update(negative)

    @classmethod
    def catmull_rom_velocity_profiles(cls, lengths, durations, speeds):
        """Interpolate speed with time-aware Catmull-Rom knot tangents.

        A zero-value, zero-slope quartic correction preserves segment distance
        without rescaling target speeds or breaking acceleration continuity.
        The public ``catmull_rom`` mode refers to these velocity tangents.
        """
        slopes = [(speeds[1] - speeds[0]) / durations[0]]
        slopes.extend((speeds[i + 1] - speeds[i - 1]) / (durations[i - 1] + durations[i])
                      for i in range(1, len(speeds) - 1))
        slopes.append((speeds[-1] - speeds[-2]) / durations[-1])
        for i in range(1, len(speeds) - 1):
            if speeds[i] == 0:
                slopes[i] = 0.0
        fallback = set()
        while True:
            profiles = [cls.continuous_velocity_coefficients(length, duration, speeds[i], speeds[i + 1], slopes[i], slopes[i + 1])
                        for i, (length, duration) in enumerate(zip(lengths, durations))]
            negative = {i for i, profile in enumerate(profiles)
                        if i not in fallback and cls.polynomial_minimum(profile) < -1e-8}
            if not negative:
                return [None if i in fallback else profile for i, profile in enumerate(profiles)]
            for i in negative:
                slopes[i] = slopes[i + 1] = 0.0
            fallback.update(negative)

    @staticmethod
    def nonnegative_distance_speed(length, duration, v0, v1, fraction):
        """Nonnegative smoothstep ramps and a plateau, with zero end acceleration."""
        if length <= Trajectory.MIN_DISTANCE_M:
            return 0.0, 0.0
        ramp = min(duration / 4, length / (v0 + v1)) if v0 + v1 > 0 else duration / 4
        middle_speed = (length - ramp * (v0 + v1) / 2) / (duration - ramp)
        time = fraction * duration
        first_distance = ramp * (v0 + middle_speed) / 2
        if time < ramp:
            u = time / ramp
            return (ramp * (v0 * u + (middle_speed - v0) * (u ** 3 - u ** 4 / 2)),
                    v0 + (middle_speed - v0) * (3 * u ** 2 - 2 * u ** 3))
        if time > duration - ramp:
            u = (time - duration + ramp) / ramp
            return (first_distance + middle_speed * (duration - 2 * ramp)
                    + ramp * (middle_speed * u + (v1 - middle_speed) * (u ** 3 - u ** 4 / 2)),
                    middle_speed + (v1 - middle_speed) * (3 * u ** 2 - 2 * u ** 3))
        return first_distance + middle_speed * (time - ramp), middle_speed

    def as_moving_series(self) -> dict[str, list[float] | list[bool]]:
        """Map the selected time law onto the spatial path's arc length.

        Hermite preserves endpoint speeds and minimizes squared acceleration.
        Linear velocity is scaled when fixed times and target speeds disagree.
        """
        mode = validate_interpolation_mode(self.interpolation_mode)
        self.interpolation_mode = mode
        waypoints = self.sorted_waypoints()
        self.validate_moving_waypoints(waypoints)
        waypoint_speeds, _adjusted = self.resolved_waypoint_speeds()
        arc_tables = []
        lengths = []
        durations = [b.time_s - a.time_s for a, b in itertools.pairwise(waypoints)]
        for i, duration in enumerate(durations):
            arc_points = self.sampled_curve_points_for_segment_count(
                i, max(self.PROFILE_DISTANCE_SAMPLES_PER_SEGMENT,
                       4 * (math.ceil(duration / CURVE_SAMPLE_PERIOD_S) + 1)),
            )
            distances = [0.0]
            for previous, current in itertools.pairwise(arc_points):
                distances.append(distances[-1] + math.hypot(current[1] - previous[1], current[2] - previous[2]))
            arc_tables.append((arc_points, distances))
            lengths.append(distances[-1])
        profiles = (self.continuous_hermite_profiles(lengths, durations, waypoint_speeds)
                    if mode == "hermite" and len(waypoints) > 2 else None)
        if mode == "catmull_rom":
            profiles = self.catmull_rom_velocity_profiles(lengths, durations, waypoint_speeds)
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
            start_speed = waypoint_speeds[segment_index]
            end_speed = waypoint_speeds[segment_index + 1]
            average_speed = (start_speed + end_speed) / 2.0
            arc_points, distances = arc_tables[segment_index]
            length = lengths[segment_index]
            for time_s, x_m, y_m in segment_points:
                fraction = (time_s - start_time) / duration
                if mode in ("hermite", "catmull_rom"):
                    if profiles is None:
                        distance, speed = self.hermite_distance_speed(
                            length, duration, start_speed, end_speed, fraction,
                        )
                    elif profiles[segment_index] is None:
                        distance, speed = self.nonnegative_distance_speed(length, duration, start_speed, end_speed, fraction)
                    else:
                        coefficients = profiles[segment_index]
                        speed = max(0.0, self.polynomial_value(coefficients, fraction))
                        distance = duration * sum(c * fraction ** (i + 1) / (i + 1)
                                                  for i, c in enumerate(coefficients))
                elif average_speed <= self.MIN_SPEED_SUM_MPS:
                    distance = fraction * length
                    speed = length / duration
                else:
                    distance = length * (
                        start_speed * fraction + (end_speed - start_speed) * fraction ** 2 / 2
                    ) / average_speed
                    speed = (start_speed + (end_speed - start_speed) * fraction) * length / (duration * average_speed)
                distance = min(max(distance, 0.0), length)
                right = min(max(bisect_right(distances, distance), 1), len(distances) - 1)
                span = distances[right] - distances[right - 1]
                part = (distance - distances[right - 1]) / span if span > 0.0 else 0.0
                left_point, right_point = arc_points[right - 1], arc_points[right]
                x_m = left_point[1] + (right_point[1] - left_point[1]) * part
                y_m = left_point[2] + (right_point[2] - left_point[2]) * part
                sampled_points.append((time_s, x_m, y_m))
                speed_values.append(speed)
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

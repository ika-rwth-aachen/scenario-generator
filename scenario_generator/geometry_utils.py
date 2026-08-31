from __future__ import annotations

import math

CURVE_SAMPLE_PERIOD_S = 0.1
MIN_CURVE_SAMPLES_PER_SEGMENT = 8
MEASUREMENT_SNAP_DISTANCE_PX = 16.0


def normalize_angle_rad(angle: float) -> float:
    """Normalize an angle to the ``[-pi, pi)`` interval."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def heading_between(
    x_values: list[float],
    y_values: list[float],
    index: int,
    fallback: float,
) -> float:
    """Estimate local heading at ``index`` from neighboring sampled points.

    The function uses the next point where possible and the previous point at
    the end of the series. Very small movements reuse ``fallback`` to avoid
    noisy headings for stationary or near-stationary samples.
    """
    if len(x_values) < 2:
        return fallback
    if index < len(x_values) - 1:
        dx = x_values[index + 1] - x_values[index]
        dy = y_values[index + 1] - y_values[index]
    else:
        dx = x_values[index] - x_values[index - 1]
        dy = y_values[index] - y_values[index - 1]
    if math.hypot(dx, dy) < 1e-9:
        return fallback
    return normalize_angle_rad(math.atan2(dy, dx))


def catmull_rom(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    u: float,
) -> tuple[float, float]:
    """Return a Catmull-Rom spline point between ``p1`` and ``p2``.

    The implementation is intentionally geometry-neutral: road rendering and
    actor trajectory generation can both use the same interpolation primitive.
    ``u`` is the segment fraction in ``[0, 1]``.
    """
    u2 = u * u
    u3 = u2 * u
    x = 0.5 * (
        (2.0 * p1[0])
        + (-p0[0] + p2[0]) * u
        + (2.0 * p0[0] - 5.0 * p1[0] + 4.0 * p2[0] - p3[0]) * u2
        + (-p0[0] + 3.0 * p1[0] - 3.0 * p2[0] + p3[0]) * u3
    )
    y = 0.5 * (
        (2.0 * p1[1])
        + (-p0[1] + p2[1]) * u
        + (2.0 * p0[1] - 5.0 * p1[1] + 4.0 * p2[1] - p3[1]) * u2
        + (-p0[1] + 3.0 * p1[1] - 3.0 * p2[1] + p3[1]) * u3
    )
    return x, y


def smoothstep(fraction: float) -> float:
    """Return cubic smoothstep interpolation weight for ``fraction``."""
    return fraction * fraction * (3.0 - 2.0 * fraction)


def interpolate_series(
    time_values: list[float],
    values: list[float],
    time_s: float,
) -> float:
    """Linearly interpolate a scalar time series with endpoint clamping."""
    if not time_values or not values:
        raise ValueError("Cannot interpolate an empty series.")
    if len(time_values) != len(values):
        raise ValueError("Time and value series must have the same length.")
    if time_s <= time_values[0]:
        return float(values[0])
    if time_s >= time_values[-1]:
        return float(values[-1])
    for index in range(1, len(time_values)):
        if time_values[index] < time_s:
            continue
        left = index - 1
        span = time_values[index] - time_values[left]
        fraction = 0.0 if span == 0.0 else (time_s - time_values[left]) / span
        return (
            float(values[left])
            + (float(values[index]) - float(values[left])) * fraction
        )
    return float(values[-1])


def interpolate_angle(
    time_values: list[float],
    values: list[float],
    time_s: float,
) -> float:
    """Interpolate an angular time series across wrap-around boundaries."""
    if not time_values or not values:
        raise ValueError("Cannot interpolate an empty series.")
    if len(time_values) != len(values):
        raise ValueError("Time and value series must have the same length.")
    if time_s <= time_values[0]:
        return float(values[0])
    if time_s >= time_values[-1]:
        return float(values[-1])
    for index in range(1, len(time_values)):
        if time_values[index] < time_s:
            continue
        left = index - 1
        span = time_values[index] - time_values[left]
        fraction = 0.0 if span == 0.0 else (time_s - time_values[left]) / span
        delta = normalize_angle_rad(float(values[index]) - float(values[left]))
        return normalize_angle_rad(float(values[left]) + delta * fraction)
    return float(values[-1])

from __future__ import annotations

import math

from scenario_generator.scenario_elements.road_user.road_user import ActorState

EPSILON = 1e-12


def dot(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Return the 2D dot product of two vectors."""
    return first[0] * second[0] + first[1] * second[1]


def projected_half_extent(actor: ActorState, axis: tuple[float, float]) -> float:
    """Project an oriented actor box onto an axis and return half its span."""
    return 0.5 * actor.length_m * abs(
        dot(axis, actor.longitudinal_axis),
    ) + 0.5 * actor.width_m * abs(dot(axis, actor.lateral_axis))


def axis_overlap_interval(
    first: ActorState,
    second: ActorState,
    axis: tuple[float, float],
    relative_velocity: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    """Return when two oriented-box projections overlap on one unit axis.

    This is one step of a swept separating-axis test. The overlap threshold is
    the sum of the boxes' projected half extents, not a circular collision
    radius.
    """
    first_center_projection_m = dot((first.x_m, first.y_m), axis)
    second_center_projection_m = dot((second.x_m, second.y_m), axis)
    center_distance_m = second_center_projection_m - first_center_projection_m
    combined_projected_half_extent_m = projected_half_extent(
        first,
        axis,
    ) + projected_half_extent(second, axis)
    if relative_velocity is None:
        relative_velocity = (
            second.vx_mps - first.vx_mps,
            second.vy_mps - first.vy_mps,
        )
    relative_speed_mps = dot(relative_velocity, axis)

    if abs(relative_speed_mps) <= EPSILON:
        if abs(center_distance_m) > combined_projected_half_extent_m:
            return None
        return -math.inf, math.inf

    first_boundary_time_s = (
        -combined_projected_half_extent_m - center_distance_m
    ) / relative_speed_mps
    second_boundary_time_s = (
        combined_projected_half_extent_m - center_distance_m
    ) / relative_speed_mps
    return (
        min(first_boundary_time_s, second_boundary_time_s),
        max(first_boundary_time_s, second_boundary_time_s),
    )


def overlap_time_for_axes(
    first: ActorState,
    second: ActorState,
    relative_velocity: tuple[float, float] | None = None,
) -> float | None:
    """Return first overlap time from a swept 2D oriented-box SAT.

    The actors' headings and velocities remain constant during extrapolation.
    Their ``collision_radius_m`` values are intentionally not used.
    """
    enter_time = 0.0
    exit_time = math.inf
    axes = (
        first.longitudinal_axis,
        first.lateral_axis,
        second.longitudinal_axis,
        second.lateral_axis,
    )
    for axis in axes:
        interval = axis_overlap_interval(
            first,
            second,
            axis,
            relative_velocity=relative_velocity,
        )
        if interval is None:
            return None
        axis_enter, axis_exit = interval
        enter_time = max(enter_time, axis_enter)
        exit_time = min(exit_time, axis_exit)
        if enter_time - exit_time > EPSILON:
            return None
    if exit_time < 0.0:
        return None
    return max(0.0, enter_time)

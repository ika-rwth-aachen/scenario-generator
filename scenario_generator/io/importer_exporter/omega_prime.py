from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import betterosi
import omega_prime

from scenario_generator.config.settings import load_omega_prime_interpolation_hz
from scenario_generator.io.importer_exporter.base import Exporter
from scenario_generator.map.map import MapPolyline
from scenario_generator.scenario_elements.road_user.detection_gap import DetectionGap
from scenario_generator.scenario_elements.road_user.road_user import (
    VehicleDimensions,
    is_ego_vehicle_name,
    safe_vehicle_name,
)
from scenario_generator.scenario_elements.road_user.trajectory import Waypoint


class OmegaPrimeAdapter(Exporter):
    """Import/export omega-prime recordings.

    The omega-prime repository defines the file format as MCAP containing ASAM
    OSI GroundTruth messages and an ASAM OpenDRIVE map.
    """

    default_suffix = ".mcap"
    format_name = "Omega-Prime"

    def export_file(
        self,
        output_path: Path,
        trajectories: dict[str, dict[str, object]],
        map_polylines: list[MapPolyline],
        map_path: Path | None = None,
        detection_gaps: list[DetectionGap] | None = None,
    ):
        """Export file.

        Args:
                output_path: output path used by this operation.
                trajectories: trajectories used by this operation.
                map_polylines: map polylines used by this operation.
                map_path: map path used by this operation.
                detection_gaps: detection gaps used by this operation.

        """
        if output_path.suffix.lower() != ".mcap":
            raise ValueError("Omega-Prime export must use a .mcap output path.")
        host_vehicle_idx = self.host_vehicle_idx_for_trajectories(trajectories)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        recording = omega_prime.Recording(
            self.trajectories_to_dataframe(trajectories, betterosi),
            map=self.load_map_odr(omega_prime, map_path),
            host_vehicle_idx=host_vehicle_idx,
            validate=False,
        )
        self.interpolate_recording(recording)
        recording.to_mcap(output_path)

    def interpolate_recording(self, recording: Any) -> None:
        """Apply the configured Omega-Prime interpolation rate."""
        recording.interpolate(hz=load_omega_prime_interpolation_hz())

    def import_file(
        self,
        input_path: Path,
    ) -> tuple[
        dict[str, list[Waypoint]],
        dict[str, VehicleDimensions],
        list[MapPolyline],
        Path | None,
        list[DetectionGap],
    ]:
        """Import file.

        Args:
                input_path: input path used by this operation.

        """
        if input_path.suffix.lower() != ".mcap":
            raise ValueError("Omega-Prime import expects a .mcap file.")
        recording = omega_prime.Recording.from_file(
            input_path,
            validate=False,
            parse_map=True,
        )
        vehicles, dimensions = self.recording_to_gui_data(recording, betterosi)
        map_polylines = self.map_polylines_from_recording(recording)
        return vehicles, dimensions, map_polylines, None, []

    def load_map_odr(self, omega_prime: Any, map_path: Path | None) -> Any | None:
        """Load map odr.

        Args:
                omega_prime: omega prime used by this operation.
                map_path: map path used by this operation.

        """
        if map_path is None or not map_path.exists():
            return None
        if map_path.suffix.lower() not in {".xodr", ".xml"}:
            return None
        return omega_prime.MapOdr.from_file(
            map_path,
            parse_map=False,
            is_odr_xml=map_path.suffix.lower() == ".xml",
        )

    def host_vehicle_idx_for_trajectories(self, trajectories: dict[str, dict[str, object]]) -> int | None:
        """Return the object index of the ego-like trajectory when one is present."""
        for object_index, (name, _) in enumerate(trajectories.items(), start=1):
            if is_ego_vehicle_name(name):
                return object_index
        return None

    def trajectories_to_dataframe(
        self,
        trajectories: dict[str, dict[str, object]],
        betterosi: Any,
    ) -> Any:
        """Handle trajectories to dataframe.

        Args:
                trajectories: trajectories used by this operation.
                betterosi: betterosi used by this operation.

        """
        import polars as pl

        rows: list[dict[str, float | int]] = []
        for object_index, (name, trajectory) in enumerate(
            trajectories.items(),
            start=1,
        ):
            actor_type = str(
                trajectory.get(
                    "actor_type",
                    self.actor_type_from_dimensions(trajectory),
                )
                or "vehicle",
            )
            dimensions = trajectory.get("dimensions")
            if not isinstance(dimensions, dict):
                dimensions = {}
            length = float(dimensions.get("length_m", 4.5))
            width = float(dimensions.get("width_m", 1.8))
            height = float(dimensions.get("height_m", 1.8))
            time_values = self.required_series(trajectory, "time_s")
            x_values = self.required_series(trajectory, "x_m")
            y_values = self.required_series(trajectory, "y_m")
            z_values = self.series_or_default(trajectory, "z_m", len(time_values), 0.0)
            yaw_values = self.series_or_default(
                trajectory,
                "yaw_rad",
                len(time_values),
                0.0,
            )
            speed_values = self.series_or_default(
                trajectory,
                "speed_mps",
                len(time_values),
                0.0,
            )
            self.validate_series_lengths(
                name,
                time_values,
                x_values,
                y_values,
                z_values,
                yaw_values,
                speed_values,
            )
            vertical_speed_values = [
                self.derivative_at(index, time_values, z_values)
                for index in range(len(time_values))
            ]
            type_value, role_value, subtype_value = self.osi_classification(
                actor_type,
                betterosi,
            )
            for index, time_s in enumerate(time_values):
                yaw = float(yaw_values[index])
                speed = float(speed_values[index])
                acc = self.acceleration_at(index, time_values, speed_values)
                vertical_speed = vertical_speed_values[index]
                vertical_acceleration = self.derivative_at(
                    index,
                    time_values,
                    vertical_speed_values,
                )
                rows.append(
                    {
                        "total_nanos": round(float(time_s) * 1_000_000_000),
                        "idx": object_index,
                        "x": float(x_values[index]),
                        "y": float(y_values[index]),
                        "z": float(z_values[index]),
                        "vel_x": speed * math.cos(yaw),
                        "vel_y": speed * math.sin(yaw),
                        "vel_z": vertical_speed,
                        "acc_x": acc * math.cos(yaw),
                        "acc_y": acc * math.sin(yaw),
                        "acc_z": vertical_acceleration,
                        "length": length,
                        "width": width,
                        "height": height,
                        "roll": 0.0,
                        "pitch": 0.0,
                        "yaw": yaw,
                        "type": type_value,
                        "role": role_value,
                        "subtype": subtype_value,
                    },
                )
        if not rows:
            raise ValueError(
                "At least one trajectory is required for Omega-Prime export.",
            )
        return pl.DataFrame(rows).sort(["total_nanos", "idx"])

    def recording_to_gui_data(
        self,
        recording: Any,
        betterosi: Any,
    ) -> tuple[dict[str, list[Waypoint]], dict[str, VehicleDimensions]]:
        """Handle recording to gui data.

        Args:
                recording: recording used by this operation.
                betterosi: betterosi used by this operation.

        """
        vehicles: dict[str, list[Waypoint]] = {}
        dimensions: dict[str, VehicleDimensions] = {}
        df = recording.df.sort(["idx", "total_nanos"])
        for object_index, group in df.group_by("idx", maintain_order=True):
            idx_value = int(
                object_index[0] if isinstance(object_index, tuple) else object_index,
            )
            name = safe_vehicle_name(f"object_{idx_value}")
            vehicles[name] = [
                Waypoint(
                    time_s=float(row["total_nanos"]) / 1_000_000_000.0,
                    x_m=float(row["x"]),
                    y_m=float(row["y"]),
                )
                for row in group.iter_rows(named=True)
            ]
            first = group.row(0, named=True)
            actor_type = self.actor_type_from_osi(int(first["type"]), betterosi)
            dimensions[name] = VehicleDimensions(
                length_m=float(first["length"]),
                width_m=float(first["width"]),
                height_m=float(first["height"]),
                actor_type=actor_type,
            )
        if not vehicles:
            raise ValueError("Omega-Prime MCAP contains no moving objects.")
        return vehicles, dimensions

    def map_polylines_from_recording(self, recording: Any) -> list[MapPolyline]:
        """Handle map polylines from recording.

        Args:
                recording: recording used by this operation.

        """
        loaded_map = getattr(recording, "map", None)
        if loaded_map is None:
            return []
        lanes = getattr(loaded_map, "lanes", None)
        if not isinstance(lanes, dict):
            return []
        polylines: list[MapPolyline] = []
        for lane_id, lane in lanes.items():
            centerline = getattr(lane, "centerline", None)
            if centerline is None:
                continue
            coords = getattr(centerline, "coords", centerline)
            points = [(float(point[0]), float(point[1])) for point in coords]
            if len(points) >= 2:
                polylines.append(MapPolyline(name=str(lane_id), points=points))
        return polylines

    def actor_type_from_dimensions(self, trajectory: dict[str, object]) -> str:
        """Handle actor type from dimensions.

        Args:
                trajectory: trajectory used by this operation.

        """
        dimensions = trajectory.get("dimensions")
        if isinstance(dimensions, dict):
            return str(dimensions.get("actor_type", "vehicle"))
        return "vehicle"

    def required_series(self, trajectory: dict[str, object], name: str) -> list[float]:
        """Handle required series.

        Args:
                trajectory: trajectory used by this operation.
                name: name used by this operation.

        """
        raw_series = trajectory.get(name)
        if not isinstance(raw_series, list):
            raise ValueError(  # noqa: TRY004 - invalid trajectory data uses the adapter error contract.
                f"Trajectory is missing {name}.",
            )
        return [float(value) for value in raw_series]

    def series_or_default(
        self,
        trajectory: dict[str, object],
        name: str,
        length: int,
        default: float,
    ) -> list[float]:
        """Handle series or default.

        Args:
                trajectory: trajectory used by this operation.
                name: name used by this operation.
                length: length used by this operation.
                default: default used by this operation.

        """
        raw_series = trajectory.get(name)
        if not isinstance(raw_series, list):
            return [default] * length
        return [float(value) for value in raw_series]

    def validate_series_lengths(self, object_name: str, *series: list[float]):
        """Validate series lengths.

        Args:
                object_name: object name used by this operation.
                *series: series used by this operation.

        """
        lengths = {len(values) for values in series}
        if len(lengths) != 1:
            raise ValueError(
                f"{object_name}: all trajectory series must have the same length.",
            )
        if 0 in lengths:
            raise ValueError(f"{object_name}: trajectory is empty.")

    def acceleration_at(
        self,
        index: int,
        time_values: list[float],
        speed_values: list[float],
    ) -> float:
        """Handle acceleration at.

        Args:
                index: index used by this operation.
                time_values: time values used by this operation.
                speed_values: speed values used by this operation.

        """
        if len(speed_values) < 2:
            return 0.0
        if index == 0:
            left, right = 0, 1
        else:
            left, right = index - 1, index
        dt = float(time_values[right]) - float(time_values[left])
        if abs(dt) < 1e-12:
            return 0.0
        return (float(speed_values[right]) - float(speed_values[left])) / dt

    def derivative_at(
        self,
        index: int,
        time_values: list[float],
        values: list[float],
    ) -> float:
        """Return a finite-difference derivative for one trajectory sample."""
        if len(values) < 2:
            return 0.0
        left, right = (0, 1) if index == 0 else (index - 1, index)
        dt = float(time_values[right]) - float(time_values[left])
        if abs(dt) < 1e-12:
            return 0.0
        return (float(values[right]) - float(values[left])) / dt

    def osi_classification(
        self,
        actor_type: str,
        betterosi: Any,
    ) -> tuple[int, int, int]:
        """Handle osi classification.

        Args:
                actor_type: actor type used by this operation.
                betterosi: betterosi used by this operation.

        """
        normalized = actor_type.lower()
        if normalized in {"pedestrian", "walker", "person"}:
            return int(betterosi.MovingObjectType.TYPE_PEDESTRIAN), -1, -1
        vehicle_type = int(betterosi.MovingObjectType.TYPE_VEHICLE)
        role = self.enum_value(
            betterosi.MovingObjectVehicleClassificationRole,
            "ROLE_UNKNOWN",
            "ROLE_OTHER",
            default=0,
        )
        if normalized in {"cyclist", "bike", "bicycle"}:
            subtype = self.enum_value(
                betterosi.MovingObjectVehicleClassificationType,
                "TYPE_BICYCLE",
                "TYPE_OTHER",
                default=0,
            )
        else:
            subtype = self.enum_value(
                betterosi.MovingObjectVehicleClassificationType,
                "TYPE_SMALL_CAR",
                "TYPE_MEDIUM_CAR",
                "TYPE_OTHER",
                default=0,
            )
        return vehicle_type, role, subtype

    def actor_type_from_osi(self, osi_type: int, betterosi: Any) -> str:
        """Handle actor type from osi.

        Args:
                osi_type: osi type used by this operation.
                betterosi: betterosi used by this operation.

        """
        if osi_type == int(betterosi.MovingObjectType.TYPE_PEDESTRIAN):
            return "pedestrian"
        return "vehicle"

    def enum_value(self, enum_type: Any, *names: str, default: int) -> int:
        """Handle enum value.

        Args:
                enum_type: enum type used by this operation.
                default: default used by this operation.
                *names: names used by this operation.

        """
        for name in names:
            if hasattr(enum_type, name):
                return int(getattr(enum_type, name))
        return default

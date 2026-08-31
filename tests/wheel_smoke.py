"""Start the installed wheel outside its source tree and check packaged resources."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

from scenario_generator.config.settings import CONFIG_PATH, load_actor_xosc_defaults


def fetch(path: str, method: str = "GET") -> bytes:
    request = Request(f"http://127.0.0.1:8000{path}", method=method)
    with urlopen(request, timeout=1) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        return response.read()


def wait_until_ready(process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Installed application exited with {process.returncode}")
        try:
            fetch("/api/scenario")
            return
        except (OSError, URLError):
            time.sleep(0.25)
    raise RuntimeError("Installed application did not become ready within 30 seconds")


def main() -> None:
    assert CONFIG_PATH.is_file()
    vehicle_defaults = load_actor_xosc_defaults("vehicle")
    assert vehicle_defaults["attributes"]["vehicleCategory"] == "car"

    with tempfile.TemporaryDirectory() as empty_directory:
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        application_command = Path(sys.executable).with_name("scenario.generator")
        process = subprocess.Popen(
            [application_command],
            cwd=Path(empty_directory),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_until_ready(process)
            assert b"scenario.generator" in fetch("/")
            assert b"<svg" in fetch("/branding/logo.svg")
            assert b"Try scenario.generator" in fetch("/docs/README.md")
            scripts = json.loads(fetch("/api/postprocessing-scripts"))
            assert len(scripts["scripts"]) >= 2
            scenario_defaults = json.loads(fetch("/api/default-scenarios"))
            assert {entry["name"] for entry in scenario_defaults["defaults"]} == {
                "cut_in_from_left.xosc",
                "cut_in_from_left_on_curved_road.json",
                "Pass_straight_intersecting_vehicle_from_right_passing_straight.json",
                "VRU_crossing_from_left.json",
            }
            map_defaults = json.loads(fetch("/api/default-maps"))
            assert {entry["name"] for entry in map_defaults["defaults"]} == {
                "highway.xodr",
                "RITA-junction.xodr",
                "roundabout.xodr",
            }
            vru_scenario = json.loads(
                fetch(
                    "/api/default-scenarios/VRU_crossing_from_left.json",
                    method="POST",
                )
            )
            assert [actor["name"] for actor in vru_scenario["actors"]] == [
                "approaching_vehicle",
                "pedestrian",
            ]
            assert vru_scenario["map"]["path"].endswith("RITA-junction.xodr")
            assert len(vru_scenario["map"]["roads"]) == 16
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                output = process.communicate(timeout=10)[0]
            except subprocess.TimeoutExpired:
                process.kill()
                output = process.communicate()[0]
            if process.returncode not in {0, -15}:
                sys.stderr.buffer.write(output)


if __name__ == "__main__":
    main()

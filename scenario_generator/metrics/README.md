# Metrics

Metrics evaluate the simulated state of two road users at a specific point in
time. The built-in TTC and THW implementations are examples. They share the
`Metric` base class in `metric.py`, which provides iteration over relevant actor
pairs and helpers for minimum values and per-actor results.

## Add a metric

Create a module in this directory and derive a class from `Metric`:

```python
from scenario_generator.metrics.metric import Metric
from scenario_generator.scenario_elements.road_user.road_user import ActorState


class ClearanceMetric(Metric):
    name = "Clearance"
    unit = "m"
    directed = False

    def pairwise(self, first: ActorState, second: ActorState) -> float | None:
        # Return None when the value is not meaningful for this pair.
        return 0.0
```

Set `directed` to `True` for source-to-target measures such as time headway;
otherwise each unordered pair is evaluated once. `pairwise` receives the
interpolated `ActorState` values, including position, yaw, speed, and vehicle
dimensions. Return `None` when no meaningful value exists.

Export the class or a small compatibility helper from `__init__.py` so the web
application can import it. To display it in the canvas, add a view setting in
`scenario_generator/webapp/static/map-editor.js` and evaluate the metric in the
`/api/metrics` endpoint in `scenario_generator/webapp/server.py`. Follow the TTC
or THW implementation as a compact example. Add focused tests in
`tests/test_metrics.py` for the metric's geometric and boundary cases.

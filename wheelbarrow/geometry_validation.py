"""Geometry validation helpers for the wheelbarrow drawing generator."""

from __future__ import annotations

from typing import Dict

import FreeCAD as App


def _assert_close(name: str, got: float, want: float, tol: float = 0.5) -> None:
    """Raise ``AssertionError`` when a measured value deviates too much."""

    if abs(got - want) > tol:
        raise AssertionError(f"[VALIDATE] {name}: got {got:.3f} mm, expected {want:.3f} ±{tol} mm")


def validate(doc: App.Document, params: Dict[str, float]) -> None:
    """Check a few critical dimensions to catch exporter/regression drift."""

    if (outer := [o for o in doc.Objects if o.Label.startswith("WHEEL_OUTER")]) and hasattr(
        outer[0], "Radius"
    ):
        _assert_close("Wheel diameter", 2.0 * outer[0].Radius, params["wheel_diameter"])

    if rails := [
        o
        for o in doc.Objects
        if o.Label.startswith("RAIL_LEFT_PROFILE") or o.Label.startswith("RAIL_RIGHT_PROFILE")
    ]:
        rail = rails[0]
        shape = getattr(rail, "Shape", None)
        if shape is not None and not shape.isNull():
            bbox = shape.BoundBox
            _assert_close("Rail length", bbox.XLength, params["rail_length"], tol=0.8)
            if not (
                params["rail_width_front"] <= bbox.YLength <= params["rail_width_rear"] + 1.0
            ):
                raise AssertionError(
                    f"[VALIDATE] Rail bbox.YLength={bbox.YLength:.2f} mm unexpected range"
                )

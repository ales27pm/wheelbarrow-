"""SVG tiling utilities for the wheelbarrow drawing generator."""

from __future__ import annotations

import math
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Tuple

from xml.etree.ElementTree import register_namespace

try:  # pragma: no cover - optional dependency when tiling is not used
    from defusedxml import ElementTree as ET
except ImportError:  # pragma: no cover - handled during runtime invocation
    ET = None  # type: ignore[assignment]

_DEFUSED_MISSING_ERROR = (
    "defusedxml is required for SVG tiling. Install defusedxml and ensure it is on PYTHONPATH."
)

_DIMENSION_RE = re.compile(r"^([0-9]*\.?[0-9]+)")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _extract_dimension(value: str) -> float:
    match = _DIMENSION_RE.match(value or "")
    return float(match.group(1)) if match else 0.0


def tile_svg_to_a4(svg_in: str, out_dir: str, paper_size: Tuple[float, float], *, overlap_mm: float = 6.0) -> None:
    """Split an SVG into overlapping tiles sized for A4 printing."""

    if ET is None:  # pragma: no cover - depends on optional dependency
        raise ImportError(_DEFUSED_MISSING_ERROR)

    input_path = Path(svg_in)
    output_dir = Path(out_dir)
    _ensure_dir(output_dir)

    tree = ET.parse(os.fspath(input_path))
    root = tree.getroot()

    if match := re.match(r"^\{(.+)\}", root.tag):
        register_namespace("", match.group(1))

    if view_box := root.get("viewBox"):
        vx, vy, vw, vh = map(float, view_box.split())
    else:
        width_attr = root.get("width", "0")
        height_attr = root.get("height", "0")
        vx, vy, vw, vh = 0.0, 0.0, _extract_dimension(width_attr), _extract_dimension(height_attr)

    a4w, a4h = paper_size
    step_x = a4w - overlap_mm
    step_y = a4h - overlap_mm

    cols = max(1, int(math.ceil((vw + overlap_mm) / step_x)))
    rows = max(1, int(math.ceil((vh + overlap_mm) / step_y)))

    for row in range(rows):
        for col in range(cols):
            x0 = vx + col * step_x
            y0 = vy + row * step_y

            tile_root = deepcopy(root)
            tile_root.set("width", f"{a4w:.3f}mm")
            tile_root.set("height", f"{a4h:.3f}mm")
            tile_root.set("viewBox", f"{x0:.3f} {y0:.3f} {a4w:.3f} {a4h:.3f}")

            out_path = output_dir / f"tile_r{row + 1}_c{col + 1}.svg"
            ET.ElementTree(tile_root).write(os.fspath(out_path), encoding="utf-8", xml_declaration=True)

    print(f"[OK] Tiled into {rows}×{cols} A4 SVG pages at: {output_dir}")

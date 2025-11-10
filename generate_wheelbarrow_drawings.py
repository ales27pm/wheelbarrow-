#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate 1:1 fabrication drawings for the mini wooden wheelbarrow.

This module reproduces the headless export pipeline that previously lived only
inside the `MiniWheelbarrow.FCMacro` macro. Running it with ``FreeCADCmd``
creates 2D profiles for every part (rails, tray panels, spreaders, legs, axle
block, and wheel) and exports them as DXF/SVG files. A consolidated PDF sheet is
produced via TechDraw when the workbench is available; otherwise a Qt-based
fallback renders a simplified outline-only PDF so CI jobs still publish a PDF
artifact.

The script defaults to generating the original 1:1 drawings, but all linear
dimensions can be scaled uniformly via ``--scale``. Scaling affects every
numerical parameter as well as the layout spacing, ensuring that the exported
files remain non-overlapping at any size.
"""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import sys
import tempfile
import time
import contextlib
import re
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

# --- FreeCAD / Workbenches ---
import FreeCAD as App
import Draft
import Import

try:  # TechDraw is optional (only required for PDF generation)
    import TechDraw

    TECHDRAW_AVAILABLE = True
except Exception:  # pragma: no cover - FreeCADCmd without TechDraw hits here
    TECHDRAW_AVAILABLE = False

from wheelbarrow import export_prefs
from wheelbarrow.geometry_validation import validate as validate_geometry
from wheelbarrow.svg_tiling import tile_svg_to_a4

DOC_NAME = "WheelbarrowDrawings"


# -----------------------------
# Geometric parameters (mm)
# -----------------------------
DEFAULT_PARAMS: Dict[str, float] = {
    # Rails / handles
    "rail_length": 500,
    "rail_width_rear": 40,
    "rail_width_front": 25,
    "rail_taper_start": 300,
    "axle_from_front": 40,
    "axle_diameter": 6,

    # Box
    "box_inner_length": 260,
    "box_inner_width_rear": 150,
    "box_inner_width_front": 70,
    "box_inner_depth": 70,

    # Spreaders / legs / axle block
    "spreader_length": 120,
    "spreader_height": 30,
    "leg_height": 70,
    "leg_width": 25,
    "block_length": 80,
    "block_width": 25,

    # Wheel
    "wheel_diameter": 120,
    "wheel_hole_diameter": 6,

    # Materials (for annotation text)
    "wood_thick_struct": 15,
    "wood_thick_panels": 10,
}


# ----------------------------------
# Page layout / export configuration
# ----------------------------------
PAPER_SIZES_MM = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "Tabloid": (279.4, 431.8),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
}

DEFAULT_PAPER = "A4"


# -----------------------------
# Helpers
# -----------------------------
Vector2D = Tuple[float, float]
DimSpec = Tuple[Vector2D, Vector2D, Vector2D]
TextSpec = Tuple[str, Vector2D, float]


def ensure_dir(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


_FONTCONFIG_CONFIGURED = False


def configure_fontconfig() -> None:
    """Ensure Fontconfig can locate fonts when running headless."""

    global _FONTCONFIG_CONFIGURED

    if _FONTCONFIG_CONFIGURED:
        return

    if os.environ.get("FONTCONFIG_FILE"):
        _FONTCONFIG_CONFIGURED = True
        return

    try:
        resource_dir = App.getResourceDir()
    except Exception as exc:  # pragma: no cover - defensive against FreeCAD issues
        print(f"[WARN] Unable to resolve FreeCAD resource directory: {exc}")
        return

    # FreeCAD AppImages bundle a complete fontconfig tree under etc/fonts. Locate
    # that configuration relative to the resource directory when available.
    app_root = os.path.abspath(os.path.join(resource_dir, os.pardir, os.pardir))
    fontconfig_candidates = [
        os.path.join(app_root, "etc", "fonts", "fonts.conf"),
        os.path.join(app_root, "usr", "etc", "fonts", "fonts.conf"),
    ]

    for candidate in fontconfig_candidates:
        if os.path.exists(candidate):
            fonts_dir = os.path.dirname(candidate)
            cache_dir = os.path.join(tempfile.gettempdir(), "fontconfig-cache")
            ensure_dir(cache_dir)
            os.environ.setdefault("FONTCONFIG_FILE", candidate)
            os.environ.setdefault("FONTCONFIG_PATH", fonts_dir)
            os.environ.setdefault("FONTCONFIG_SYS_CACHE_DIR", cache_dir)
            os.environ.setdefault("FONTCONFIG_SYS_MONO_CACHE_DIR", cache_dir)
            _FONTCONFIG_CONFIGURED = True
            return

    print(
        "[WARN] Fontconfig configuration could not be located; TechDraw exports "
        "may fail."
    )


def _set_view_properties(obj: App.DocumentObject, **props: float) -> None:
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return
    for key, value in props.items():
        if hasattr(view, key):
            setattr(view, key, value)


def recompute(doc: App.Document) -> None:
    try:
        doc.recompute()
    except Exception as exc:
        print(f"[WARN] Document recompute failed: {exc}")


def add_text(doc: App.Document, text: str, pos: Vector2D, *, size: float = 4.0) -> App.DocumentObject:
    annotation = Draft.make_text([text], App.Vector(pos[0], pos[1], 0))
    _set_view_properties(annotation, FontSize=size)
    return annotation


def add_linear_dimension(doc: App.Document, p1: Vector2D, p2: Vector2D, dim_pos: Vector2D) -> App.DocumentObject:
    v1 = App.Vector(p1[0], p1[1], 0)
    v2 = App.Vector(p2[0], p2[1], 0)
    vdim = App.Vector(dim_pos[0], dim_pos[1], 0)
    dim = Draft.make_dimension(v1, v2, vdim)
    _set_view_properties(
        dim,
        FontSize=3.0,
        ArrowSize=2.0,
        ExtLines=1,
        ShowUnit=False,
    )
    return dim


def add_diameter_dimension(doc: App.Document, center: Vector2D, radius: float, dim_pos: Vector2D) -> App.DocumentObject:
    cx, cy = center
    r = radius
    p1 = (cx - r, cy)
    p2 = (cx + r, cy)
    return add_linear_dimension(doc, p1, p2, dim_pos)


def polygon_wire(points: Iterable[Vector2D]) -> App.DocumentObject:
    vectors = [App.Vector(x, y, 0) for (x, y) in points]
    return Draft.make_wire(vectors, closed=True)


def circle(center: Vector2D, radius: float) -> App.DocumentObject:
    placement = App.Placement(App.Vector(center[0], center[1], 0), App.Rotation(0, 0, 0))
    return Draft.make_circle(radius=radius, placement=placement)


def rectangle_xywh(x: float, y: float, w: float, h: float) -> App.DocumentObject:
    pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return polygon_wire(pts)


def annotate(
    doc: App.Document,
    *,
    dims: Sequence[DimSpec] | None = None,
    texts: Sequence[TextSpec] | None = None,
) -> None:
    """Create Draft dimensions and annotations in a single batch."""

    for start, end, location in dims or ():
        add_linear_dimension(doc, start, end, location)
    for content, position, size in texts or ():
        add_text(doc, content, position, size=size)


def arc_top_panel(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    arc_height: float = 10.0,
    segments: int = 24,
) -> App.DocumentObject:
    body_h = h - arc_height
    pts: List[Vector2D] = [(x, y), (x + w, y), (x + w, y + body_h)]

    f = arc_height
    c = w
    R = (f**2 + (c / 2.0) ** 2.0) / (2.0 * f)
    x_c = x + w / 2.0
    y_c = y + body_h - (R - f)

    A = (x, y + body_h)
    B = (x + w, y + body_h)

    def ang(pt: Vector2D) -> float:
        return math.degrees(math.atan2(pt[1] - y_c, pt[0] - x_c))

    angA = ang(A)
    angB = ang(B)
    if angB < angA:
        angB += 360.0

    for i in range(segments + 1):
        t = angA + (angB - angA) * (i / segments)
        rad = math.radians(t)
        px = x_c + R * math.cos(rad)
        py = y_c + R * math.sin(rad)
        pts.append((px, py))

    pts.extend([(x, y + body_h), (x, y)])
    return polygon_wire(pts)


# --------------------------------
# Part geometry generation
# --------------------------------
def make_rails(
    doc: App.Document,
    params: Dict[str, float],
    *,
    origin: Vector2D = (0.0, 0.0),
    mirror_x: bool = False,
    label: str = "RAIL",
) -> List[App.DocumentObject]:
    L = params["rail_length"]
    w0 = params["rail_width_rear"]
    w1 = params["rail_width_front"]
    x_taper = params["rail_taper_start"]
    axle_from_front = params["axle_from_front"]
    axle_d = params["axle_diameter"]

    def width_at(x: float) -> float:
        if x <= x_taper:
            return w0
        m = (w1 - w0) / (L - x_taper)
        return w0 + m * (x - x_taper)

    N = 60
    top_pts = [(0.0, 0.0), (L, 0.0)]
    bottom_pts = []
    for i in range(N + 1):
        x = L - (L * i / N)
        y = -width_at(x)
        bottom_pts.append((x, y))
    outline = top_pts + bottom_pts + [(0.0, 0.0)]

    def xform(pt: Vector2D) -> Vector2D:
        x = -pt[0] if mirror_x else pt[0]
        return origin[0] + x, origin[1] + pt[1]

    outline2 = [xform(p) for p in outline]
    wire = polygon_wire(outline2)
    wire.Label = f"{label}_PROFILE"

    x_axle_local = L - axle_from_front
    w_axle = width_at(x_axle_local)
    y_axle_local = -w_axle / 2.0
    cx, cy = xform((x_axle_local, y_axle_local))
    circ = circle(center=(cx, cy), radius=axle_d / 2.0)
    circ.Label = f"{label}_AXLE_HOLE"

    annotate(
        doc,
        dims=[
            (
                xform((0.0, -w0 - 6.0)),
                xform((L, -w1 - 6.0)),
                xform((L / 2.0, -w0 - 14.0)),
            ),
            (xform((0.0, 0.0)), xform((0.0, -w0)), xform((-15.0, -w0 / 2.0))),
            (xform((L, 0.0)), xform((L, -w1)), xform((L + 15.0, -w1 / 2.0))),
            (
                xform((L, -w1 - 20.0)),
                xform((x_axle_local, -w1 - 20.0)),
                xform(((L + x_axle_local) / 2.0, -w1 - 28.0)),
            ),
        ],
        texts=[
            (
                f"{label} — L={L}  w_rear={w0}  w_front={w1}  axle Ø{axle_d}",
                (origin[0] + (L * 0.5), origin[1] + 8.0),
                4.0,
            )
        ],
    )
    add_diameter_dimension(doc, (cx, cy), axle_d / 2.0, (cx, cy + 12.0))

    recompute(doc)
    return [wire, circ]


def make_trapezoid_bottom(
    doc: App.Document,
    params: Dict[str, float],
    *,
    origin: Vector2D = (0.0, 0.0),
    label: str = "BOTTOM",
) -> List[App.DocumentObject]:
    L = params["box_inner_length"]
    wr = params["box_inner_width_rear"]
    wf = params["box_inner_width_front"]

    pts = [
        (origin[0] + 0.0, origin[1] + wr / 2.0),
        (origin[0] + L, origin[1] + wf / 2.0),
        (origin[0] + L, origin[1] - wf / 2.0),
        (origin[0] + 0.0, origin[1] - wr / 2.0),
    ]
    wire = polygon_wire(pts)
    wire.Label = f"{label}_TRAPEZOID"

    annotate(
        doc,
        dims=[
            (
                (origin[0], origin[1] - wr / 2.0 - 10.0),
                (origin[0] + L, origin[1] - wr / 2.0 - 10.0),
                (origin[0] + L / 2.0, origin[1] - wr / 2.0 - 18.0),
            ),
            (
                (origin[0], origin[1] + wr / 2.0),
                (origin[0], origin[1] - wr / 2.0),
                (origin[0] - 12.0, origin[1]),
            ),
            (
                (origin[0] + L, origin[1] + wf / 2.0),
                (origin[0] + L, origin[1] - wf / 2.0),
                (origin[0] + L + 12.0, origin[1]),
            ),
        ],
        texts=[
            (
                f"{label} — L={L}  Wrear={wr}  Wfront={wf}",
                (origin[0] + L / 2.0, origin[1] + wr / 2.0 + 8.0),
                4.0,
            )
        ],
    )
    recompute(doc)
    return [wire]


def make_side_panel(
    doc: App.Document,
    params: Dict[str, float],
    *,
    origin: Vector2D = (0.0, 0.0),
    label: str = "SIDE",
) -> List[App.DocumentObject]:
    L = params["box_inner_length"]
    H = params["box_inner_depth"]
    rect = rectangle_xywh(origin[0], origin[1], L, H)
    rect.Label = f"{label}_RECT"

    annotate(
        doc,
        dims=[
            (
                (origin[0], origin[1] - 8.0),
                (origin[0] + L, origin[1] - 8.0),
                (origin[0] + L / 2.0, origin[1] - 15.0),
            ),
            (
                (origin[0] + L + 8.0, origin[1]),
                (origin[0] + L + 8.0, origin[1] + H),
                (origin[0] + L + 16.0, origin[1] + H / 2.0),
            ),
        ],
        texts=[
            (
                f"{label} — {L} × {H} (panel thickness {params['wood_thick_panels']} mm)",
                (origin[0] + L / 2.0, origin[1] + H + 6.0),
                4.0,
            )
        ],
    )
    recompute(doc)
    return [rect]


def make_front_panel(
    doc: App.Document,
    params: Dict[str, float],
    *,
    origin: Vector2D = (0.0, 0.0),
    label: str = "FRONT",
    arc_height: float = 10.0,
) -> List[App.DocumentObject]:
    W = params["box_inner_width_front"]
    H = params["box_inner_depth"]
    poly = arc_top_panel(origin[0], origin[1], W, H, arc_height=arc_height, segments=36)
    poly.Label = f"{label}_CURVED_TOP"

    annotate(
        doc,
        dims=[
            (
                (origin[0], origin[1] - 8.0),
                (origin[0] + W, origin[1] - 8.0),
                (origin[0] + W / 2.0, origin[1] - 15.0),
            ),
            (
                (origin[0] + W + 8.0, origin[1]),
                (origin[0] + W + 8.0, origin[1] + H),
                (origin[0] + W + 16.0, origin[1] + H / 2.0),
            ),
        ],
        texts=[
            (
                f"{label} — {W} × {H} (curved top rise {arc_height})",
                (origin[0] + W / 2.0, origin[1] + H + 6.0),
                4.0,
            )
        ],
    )
    recompute(doc)
    return [poly]


def make_spreaders(
    doc: App.Document,
    params: Dict[str, float],
    *,
    origin: Vector2D = (0.0, 0.0),
    label: str = "SPREADER",
    gap: float = 8.0,
) -> List[App.DocumentObject]:
    L = params["spreader_length"]
    H = params["spreader_height"]
    dims: List[DimSpec] = []
    objs: List[App.DocumentObject] = []
    for i in range(2):
        rect = rectangle_xywh(origin[0], origin[1] + i * (H + gap), L, H)
        rect.Label = f"{label}_{i + 1}"
        dims.append(
            (
                (origin[0], origin[1] - gap + i * (H + gap)),
                (origin[0] + L, origin[1] - gap + i * (H + gap)),
                (origin[0] + L / 2.0, origin[1] - gap - 7.0 + i * (H + gap)),
            )
        )
        dims.append(
            (
                (origin[0] + L + gap, origin[1] + i * (H + gap)),
                (origin[0] + L + gap, origin[1] + H + i * (H + gap)),
                (origin[0] + L + gap * 2.0, origin[1] + H / 2.0 + i * (H + gap)),
            )
        )
        objs.append(rect)
    annotate(
        doc,
        dims=dims,
        texts=[
            (
                f"{label} ×2 — {L} × {H} × {params['wood_thick_struct']}",
                (origin[0] + L / 2.0, origin[1] + 2 * (H + gap) + 4.0),
                4.0,
            )
        ],
    )
    recompute(doc)
    return objs


def make_legs(
    doc: App.Document,
    params: Dict[str, float],
    *,
    origin: Vector2D = (0.0, 0.0),
    label: str = "LEG",
    gap: float = 12.0,
) -> List[App.DocumentObject]:
    H = params["leg_height"]
    W = params["leg_width"]
    dims: List[DimSpec] = []
    objs: List[App.DocumentObject] = []
    for i in range(2):
        rect = rectangle_xywh(origin[0] + i * (W + gap), origin[1], W, H)
        rect.Label = f"{label}_{i + 1}"
        dims.append(
            (
                (origin[0] + i * (W + gap), origin[1] - gap),
                (origin[0] + W + i * (W + gap), origin[1] - gap),
                (origin[0] + W / 2.0 + i * (W + gap), origin[1] - gap - 7.0),
            )
        )
        dims.append(
            (
                (origin[0] + W + gap + i * (W + gap), origin[1]),
                (origin[0] + W + gap + i * (W + gap), origin[1] + H),
                (origin[0] + W + gap * 2.0 + i * (W + gap), origin[1] + H / 2.0),
            )
        )
        objs.append(rect)
    annotate(
        doc,
        dims=dims,
        texts=[
            (
                f"{label} ×2 — {W} × {H} × {params['wood_thick_struct']}",
                (origin[0] + (W + gap), origin[1] + H + 6.0),
                4.0,
            )
        ],
    )
    recompute(doc)
    return objs


def make_block(
    doc: App.Document,
    params: Dict[str, float],
    *,
    origin: Vector2D = (0.0, 0.0),
    label: str = "AXLE_BLOCK",
) -> List[App.DocumentObject]:
    L = params["block_length"]
    W = params["block_width"]
    rect = rectangle_xywh(origin[0], origin[1], L, W)
    rect.Label = f"{label}_RECT"
    cx, cy = origin[0] + L / 2.0, origin[1] + W / 2.0
    circ = circle((cx, cy), params["axle_diameter"] / 2.0)
    circ.Label = f"{label}_HOLE"

    annotate(
        doc,
        dims=[
            (
                (origin[0], origin[1] - 8.0),
                (origin[0] + L, origin[1] - 8.0),
                (origin[0] + L / 2.0, origin[1] - 15.0),
            ),
            (
                (origin[0] + L + 8.0, origin[1]),
                (origin[0] + L + 8.0, origin[1] + W),
                (origin[0] + L + 16.0, origin[1] + W / 2.0),
            ),
        ],
        texts=[
            (
                f"{label} — {L} × {W} × {params['wood_thick_struct']}  trou Ø{params['axle_diameter']}",
                (origin[0] + L / 2.0, origin[1] + W + 6.0),
                4.0,
            )
        ],
    )
    add_diameter_dimension(doc, (cx, cy), params["axle_diameter"] / 2.0, (cx, cy + 10.0))
    recompute(doc)
    return [rect, circ]


def make_wheel(
    doc: App.Document,
    params: Dict[str, float],
    *,
    origin: Vector2D = (0.0, 0.0),
    label: str = "WHEEL",
) -> List[App.DocumentObject]:
    D = params["wheel_diameter"]
    dH = params["wheel_hole_diameter"]
    cx, cy = origin[0] + D / 2.0, origin[1] + D / 2.0
    outer = circle((cx, cy), D / 2.0)
    outer.Label = f"{label}_OUTER"
    inner = circle((cx, cy), dH / 2.0)
    inner.Label = f"{label}_HUB"

    add_diameter_dimension(doc, (cx, cy), D / 2.0, (cx, cy + D / 2.0 + 10.0))
    add_diameter_dimension(doc, (cx, cy), dH / 2.0, (cx, cy - D / 2.0 - 10.0))
    add_text(
        doc,
        f"{label} — Ø{D} (thickness {params['wood_thick_struct']} mm)  hub Ø{dH} mm",
        (cx, cy + D / 2.0 + 18.0),
        size=4.0,
    )
    recompute(doc)
    return [outer, inner]


# -----------------------------
# Placement (layout)
# -----------------------------
def layout_parts(doc: App.Document, params: Dict[str, float], *, scale: float = 1.0) -> Dict[str, List[App.DocumentObject]]:
    def offset(value: float) -> float:
        return value * scale

    x0, y0 = 0.0, 0.0

    objs: Dict[str, List[App.DocumentObject]] = {
        "rail_left": make_rails(
            doc,
            params,
            origin=(x0, y0 + offset(350.0)),
            mirror_x=False,
            label="RAIL_LEFT",
        ),
        "rail_right": make_rails(
            doc,
            params,
            origin=(x0, y0 + offset(200.0)),
            mirror_x=True,
            label="RAIL_RIGHT",
        ),
        "bottom": make_trapezoid_bottom(doc, params, origin=(0.0, y0), label="BOTTOM"),
        "side_left": make_side_panel(
            doc,
            params,
            origin=(params["box_inner_length"] + offset(20.0), y0),
            label="SIDE_LEFT",
        ),
        "side_right": make_side_panel(
            doc,
            params,
            origin=(params["box_inner_length"] + offset(20.0), y0 + params["box_inner_depth"] + offset(20.0)),
            label="SIDE_RIGHT",
        ),
        "front": make_front_panel(
            doc,
            params,
            origin=(params["box_inner_length"] * 2.0 + offset(50.0), y0),
            label="FRONT_PANEL",
        ),
        "spreaders": make_spreaders(
            doc,
            params,
            origin=(0.0, y0 + offset(520.0)),
            label="SPREADER",
            gap=offset(8.0),
        ),
        "legs": make_legs(
            doc,
            params,
            origin=(params["spreader_length"] + offset(30.0), y0 + offset(520.0)),
            label="LEG",
            gap=offset(12.0),
        ),
        "axle_block": make_block(
            doc,
            params,
            origin=(params["spreader_length"] + offset(140.0), y0 + offset(520.0)),
            label="AXLE_BLOCK",
        ),
        "wheel": make_wheel(
            doc,
            params,
            origin=(params["rail_length"] + offset(40.0), y0 + offset(220.0)),
            label="WHEEL",
        ),
    }

    recompute(doc)
    return objs


# -----------------------------
# Export helpers
# -----------------------------
def export_group(objects: Iterable[App.DocumentObject], out_path_base: str) -> None:
    """Export the provided Draft objects to DXF and SVG files.

    ``importDXF``/``importSVG`` offer better fidelity for 2D outputs in modern
    FreeCAD packages, but ``Import.export`` remains the universal fallback.
    Each exporter must leave a real file behind; otherwise the fallback is
    invoked automatically and the failure is reported.
    """

    def _remove_existing(path: str) -> None:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as exc:  # pragma: no cover - depends on FS state
                raise RuntimeError(f"Could not overwrite existing file {path}: {exc}")

    def _attempt_named_export(
        module_name: str,
        objs: Sequence[App.DocumentObject],
        path: str,
        *,
        label: str,
    ) -> Tuple[bool, str | None]:
        try:
            module = __import__(module_name)
        except Exception as exc:  # pragma: no cover - module availability varies
            return False, f"{module_name} unavailable: {exc}"

        try:
            module.export(objs, path)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - env specific
            return False, f"{module_name}.export failed for {label}: {exc}"

        if not os.path.exists(path):
            return False, f"{module_name}.export reported success but {path} missing"
        return True, None

    def _attempt_draft_export(
        objs: Sequence[App.DocumentObject], path: str, *, label: str
    ) -> Tuple[bool, str | None]:
        try:
            Draft.export(objs, path)
        except Exception as exc:  # pragma: no cover - env specific
            return False, f"Draft.export failed for {label}: {exc}"
        if not os.path.exists(path):
            return False, f"Draft.export reported success but {path} missing"
        return True, None

    def _attempt_import_export(
        objs: Sequence[App.DocumentObject], path: str, *, label: str
    ) -> Tuple[bool, str | None]:
        try:
            Import.export(objs, path)
        except Exception as exc:  # pragma: no cover - env specific
            return False, f"Import.export failed for {label}: {exc}"
        if not os.path.exists(path):
            return False, f"Import.export reported success but {path} missing"
        return True, None

    objs = list(objects)
    if not objs:
        raise ValueError(f"No objects provided for export to {out_path_base}")

    dxf_path = f"{out_path_base}.dxf"
    svg_path = f"{out_path_base}.svg"

    for path in (dxf_path, svg_path):
        _remove_existing(path)

    errors: List[str] = []

    def _run_export_chain(*attempts: Tuple[str, Callable[[], Tuple[bool, str | None]]]) -> None:
        messages: List[str] = []
        last_index = len(attempts) - 1
        for idx, (attempt_name, runner) in enumerate(attempts):
            success, warn_msg = runner()
            if success:
                return
            if idx < last_index:
                if warn_msg:
                    print(f"[WARN] {warn_msg}")
            else:
                messages.append(
                    warn_msg if warn_msg else f"{attempt_name} export failed without details"
                )
        if messages:
            errors.extend(messages)

    def _make_runner(func, *args, **kwargs) -> Callable[[], Tuple[bool, str | None]]:
        return lambda: func(*args, **kwargs)

    _run_export_chain(
        (
            "importDXF",
            _make_runner(
                _attempt_named_export,
                "importDXF",
                objs,
                dxf_path,
                label=f"{out_path_base}.dxf",
            ),
        ),
        (
            "Draft",
            _make_runner(
                _attempt_draft_export,
                objs,
                dxf_path,
                label=f"{out_path_base}.dxf",
            ),
        ),
        (
            "Import",
            _make_runner(
                _attempt_import_export,
                objs,
                dxf_path,
                label=f"{out_path_base}.dxf",
            ),
        ),
    )

    _run_export_chain(
        (
            "importSVG",
            _make_runner(
                _attempt_named_export,
                "importSVG",
                objs,
                svg_path,
                label=f"{out_path_base}.svg",
            ),
        ),
        (
            "Draft",
            _make_runner(
                _attempt_draft_export,
                objs,
                svg_path,
                label=f"{out_path_base}.svg",
            ),
        ),
        (
            "Import",
            _make_runner(
                _attempt_import_export,
                objs,
                svg_path,
                label=f"{out_path_base}.svg",
            ),
        ),
    )

    if errors:
        raise RuntimeError("; ".join(errors))


PDF_BACKENDS = ("techdraw", "qt", "auto")

TECHDRAW_TEMPLATE_MAP = {
    "A4": {"titleblock": "A4_Landscape_TD.svg", "blank": "A4_Landscape_blank.svg"},
    "A3": {"titleblock": "A3_Landscape_TD.svg", "blank": "A3_Landscape_blank.svg"},
    "Tabloid": {"titleblock": "ANSIB_Landscape.svg", "blank": "ANSIB_Landscape_blank.svg"},
    "Letter": {"titleblock": "ANSIA_Landscape.svg", "blank": "ANSIA_Landscape_blank.svg"},
    "Legal": {"titleblock": "ANSIB_Portrait.svg", "blank": None},
}

TECHDRAW_QT_APP: object | None = None


def make_pdf_page_from_objects(
    doc: App.Document,
    objects: Iterable[App.DocumentObject],
    *,
    paper: str = "A4",
    title: str = "Sheet",
    out_pdf_path: str | None = None,
    with_titleblock: bool = True,
    scale_hint: float = 1.0,
    pdf_backend: str = "techdraw",
) -> None:
    objects = list(objects)

    def _blank_template_svg(width_mm: float, height_mm: float) -> str:
        return (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<svg width=\"{width}mm\" height=\"{height}mm\" "
            "viewBox=\"0 0 {width} {height}\" version=\"1.1\" "
            "xmlns=\"http://www.w3.org/2000/svg\" "
            "xmlns:cc=\"http://creativecommons.org/ns#\" "
            "xmlns:dc=\"http://purl.org/dc/elements/1.1/\" "
            "xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">\n"
            " <metadata>\n"
            "  <rdf:RDF>\n"
            "   <cc:Work rdf:about=\"\">\n"
            "    <dc:format>image/svg+xml</dc:format>\n"
            "    <dc:type rdf:resource=\"http://purl.org/dc/dcmitype/StillImage\"/>\n"
            "   </cc:Work>\n"
            "  </rdf:RDF>\n"
            " </metadata>\n"
            "</svg>\n"
        ).format(width=f"{width_mm:.3f}", height=f"{height_mm:.3f}")

    def _sanitize_for_filename(*parts: str) -> str:
        cleaned: List[str] = []
        for part in parts:
            token = part.lower().replace(" ", "_").replace("/", "-")
            cleaned.append(token)
        return "_".join(cleaned)

    def _get_standard_template_path(template_name: str) -> str:
        resource_dir = App.getResourceDir()
        candidate = os.path.join(resource_dir, "Mod", "TechDraw", "Templates", template_name)
        if not os.path.exists(candidate):
            raise FileNotFoundError(
                f"TechDraw template '{template_name}' not found under {resource_dir}"
            )
        return candidate

    def _resolve_template_path(width_mm: float, height_mm: float) -> str:
        mapping = TECHDRAW_TEMPLATE_MAP.get(paper) or TECHDRAW_TEMPLATE_MAP[DEFAULT_PAPER]
        key = "titleblock" if with_titleblock else "blank"
        template_name = mapping.get(key)
        if template_name:
            return _get_standard_template_path(template_name)

        cache_root = os.path.join(
            os.path.dirname(out_pdf_path) if out_pdf_path else tempfile.gettempdir(),
            "_techdraw_templates",
        )
        ensure_dir(cache_root)
        file_name = _sanitize_for_filename(
            paper,
            "blank",
            f"{width_mm:.1f}".replace(".", "_"),
            f"{height_mm:.1f}".replace(".", "_"),
        )
        template_path = os.path.join(cache_root, f"{file_name}.svg")
        if not os.path.exists(template_path):
            with open(template_path, "w", encoding="utf-8") as handle:
                handle.write(_blank_template_svg(width_mm, height_mm))
        return template_path

    def _ensure_techdraw_gui() -> Callable[[App.DocumentObject, str], None]:
        global TECHDRAW_QT_APP

        configure_fontconfig()

        qt_widgets = None
        try:
            from PySide2 import QtWidgets  # type: ignore[import]
            qt_widgets = QtWidgets
        except Exception:
            try:
                from PySide6 import QtWidgets  # type: ignore[import,no-redef]
                qt_widgets = QtWidgets
            except Exception as exc:  # pragma: no cover - depends on runtime env
                raise RuntimeError(f"Qt widgets unavailable for TechDraw export: {exc}")

        if TECHDRAW_QT_APP is None:
            TECHDRAW_QT_APP = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
            TECHDRAW_QT_APP.setQuitOnLastWindowClosed(False)

        import FreeCADGui

        try:
            FreeCADGui.setupWithoutGUI()
        except Exception:
            # setupWithoutGUI raises if already initialised; ignore to allow reuse.
            pass

        import TechDrawGui  # type: ignore[import]

        if not hasattr(TechDrawGui, "exportPageAsPdf"):
            raise RuntimeError("TechDrawGui lacks exportPageAsPdf; cannot produce PDF.")

        return TechDrawGui.exportPageAsPdf

    def _make_doc_name(prefix: str) -> str:
        token = re.sub(r"[^0-9A-Za-z_]+", "_", prefix).strip("_") or "TechDraw"
        if token[0].isdigit():
            token = f"_{token}"
        base = token
        index = 1
        while doc.getObject(token) is not None:
            token = f"{base}_{index}"
            index += 1
        return token

    def _techdraw_pdf() -> None:
        w, h = PAPER_SIZES_MM.get(paper, PAPER_SIZES_MM[DEFAULT_PAPER])

        page_name = _make_doc_name(f"{title}_Page")
        template_name = _make_doc_name(f"{title}_Template")
        view_name = _make_doc_name(f"{title}_DraftView")

        page = doc.addObject("TechDraw::DrawPage", page_name)
        template = doc.addObject("TechDraw::DrawSVGTemplate", template_name)

        template.Template = _resolve_template_path(w, h)
        page.Template = template

        created_views: List[App.DocumentObject] = []

        try:
            for index, obj in enumerate(objects, start=1):
                candidate_name = _make_doc_name(f"{title}_DraftView_{index}")

                view_type = "TechDraw::DrawViewDraft"
                shape = getattr(obj, "Shape", None)
                if shape is not None and not getattr(shape, "isNull", lambda: True)():
                    view_type = "TechDraw::DrawViewPart"

                view = doc.addObject(view_type, candidate_name)

                try:
                    if view_type == "TechDraw::DrawViewPart":
                        # DrawViewPart expects a sequence of sources; fall back to a
                        # single link assignment if the FreeCAD build rejects lists.
                        try:
                            view.Source = [obj]
                        except TypeError:
                            view.Source = obj
                    else:
                        view.Source = obj
                except TypeError as exc:
                    raise RuntimeError(
                        f"Unable to attach object '{obj.Label}' to TechDraw view"
                    ) from exc

                view.XDirection = App.Vector(1, 0, 0)
                view.YDirection = App.Vector(0, 1, 0)
                view.ScaleType = "Custom"
                view.Scale = 1.0

                page.addView(view)
                created_views.append(view)

            if not created_views:
                raise RuntimeError("No TechDraw views were created for PDF export.")

            recompute(doc)

            if out_pdf_path:
                abs_pdf_path = os.path.abspath(out_pdf_path)
                if os.path.exists(abs_pdf_path):
                    try:
                        os.remove(abs_pdf_path)
                    except OSError as exc:
                        raise RuntimeError(
                            f"Unable to remove existing PDF before TechDraw export: {exc}"
                        ) from exc

                App.setActiveDocument(doc.Name)
                App.ActiveDocument = doc

                with contextlib.suppress(Exception):
                    import FreeCADGui

                    gui_doc = FreeCADGui.getDocument(doc.Name)
                    FreeCADGui.setActiveDocument(doc.Name)
                    FreeCADGui.ActiveDocument = gui_doc
                    FreeCADGui.activateWorkbench("TechDrawWorkbench")

                last_exc: Exception | None = None

                if hasattr(TechDraw, "exportPageAsPdf"):
                    try:
                        TechDraw.exportPageAsPdf(page, abs_pdf_path)
                    except Exception as exc:  # pragma: no cover - depends on FreeCAD build
                        last_exc = exc
                    else:
                        last_exc = None

                if last_exc is not None or not os.path.exists(abs_pdf_path):
                    exporter = _ensure_techdraw_gui()
                    try:
                        exporter(page, abs_pdf_path)
                        last_exc = None
                    except Exception as exc:  # pragma: no cover - depends on FreeCAD build
                        last_exc = exc

                if last_exc is not None:
                    raise RuntimeError(f"TechDraw export failed: {last_exc}")

                deadline = time.time() + 10.0
                while time.time() < deadline:
                    if os.path.exists(abs_pdf_path) and os.path.getsize(abs_pdf_path) > 0:
                        break
                    if TECHDRAW_QT_APP is not None:
                        TECHDRAW_QT_APP.processEvents()
                    time.sleep(0.1)

                if not os.path.exists(abs_pdf_path) or os.path.getsize(abs_pdf_path) == 0:
                    raise RuntimeError(
                        f"TechDraw export did not produce a PDF at {abs_pdf_path}."
                    )
        finally:
            # Remove temporary TechDraw artefacts so repeated runs keep the
            # document tidy and avoid name collisions.
            for view in created_views:
                with contextlib.suppress(Exception):
                    if doc.getObject(view.Name) is not None:
                        doc.removeObject(view.Name)
            with contextlib.suppress(Exception):
                if doc.getObject(template.Name) is not None:
                    doc.removeObject(template.Name)
            with contextlib.suppress(Exception):
                if doc.getObject(page.Name) is not None:
                    doc.removeObject(page.Name)

        recompute(doc)
        if out_pdf_path:
            abs_pdf_path = os.path.abspath(out_pdf_path)
            if os.path.exists(abs_pdf_path):
                try:
                    os.remove(abs_pdf_path)
                except OSError as exc:
                    raise RuntimeError(
                        f"Unable to remove existing PDF before TechDraw export: {exc}"
                    ) from exc

            App.setActiveDocument(doc.Name)
            App.ActiveDocument = doc

            with contextlib.suppress(Exception):
                import FreeCADGui

                gui_doc = FreeCADGui.getDocument(doc.Name)
                FreeCADGui.setActiveDocument(doc.Name)
                FreeCADGui.ActiveDocument = gui_doc
                FreeCADGui.activateWorkbench("TechDrawWorkbench")

            last_exc: Exception | None = None

            if hasattr(TechDraw, "exportPageAsPdf"):
                try:
                    TechDraw.exportPageAsPdf(page, abs_pdf_path)
                except Exception as exc:  # pragma: no cover - depends on FreeCAD build
                    last_exc = exc
                else:
                    last_exc = None

            if last_exc is not None or not os.path.exists(abs_pdf_path):
                exporter = _ensure_techdraw_gui()
                try:
                    exporter(page, abs_pdf_path)
                    last_exc = None
                except Exception as exc:  # pragma: no cover - depends on FreeCAD build
                    last_exc = exc

            if last_exc is not None:
                raise RuntimeError(f"TechDraw export failed: {last_exc}")

            deadline = time.time() + 10.0
            while time.time() < deadline:
                if os.path.exists(abs_pdf_path) and os.path.getsize(abs_pdf_path) > 0:
                    break
                if TECHDRAW_QT_APP is not None:
                    TECHDRAW_QT_APP.processEvents()
                time.sleep(0.1)

            if not os.path.exists(abs_pdf_path) or os.path.getsize(abs_pdf_path) == 0:
                raise RuntimeError(
                    f"TechDraw export did not produce a PDF at {abs_pdf_path}."
                )

    def _qt_pdf_fallback() -> None:
        if out_pdf_path is None:
            print("[INFO] TechDraw unavailable and no PDF path supplied; skipping PDF export.")
            return

        try:
            from PySide2 import QtCore, QtGui, QtPrintSupport
        except Exception:  # pragma: no cover - depends on FreeCAD build
            try:
                from PySide6 import QtCore, QtGui, QtPrintSupport  # type: ignore[no-redef]
            except Exception as exc:  # pragma: no cover - depends on FreeCAD build
                raise RuntimeError(f"Qt bindings unavailable for PDF fallback: {exc}")

        shapes = [getattr(obj, "Shape", None) for obj in objects]
        shapes = [shape for shape in shapes if shape is not None and not shape.isNull()]
        if not shapes:
            raise RuntimeError("No shapes available for PDF fallback export.")

        bbox = shapes[0].BoundBox
        for shape in shapes[1:]:
            bbox.add(shape.BoundBox)

        page_w_mm, page_h_mm = PAPER_SIZES_MM.get(paper, PAPER_SIZES_MM[DEFAULT_PAPER])
        margin_top_mm = 18.0 if with_titleblock else 10.0
        margin_side_mm = 10.0
        margin_bottom_mm = 12.0

        def mm_to_pt(mm: float) -> float:
            return mm * 72.0 / 25.4

        page_w_pt = mm_to_pt(page_w_mm)
        page_h_pt = mm_to_pt(page_h_mm)
        usable_w_pt = page_w_pt - 2.0 * mm_to_pt(margin_side_mm)
        usable_h_pt = page_h_pt - (mm_to_pt(margin_top_mm) + mm_to_pt(margin_bottom_mm))
        width_mm = max(bbox.XLength, 1e-3)
        height_mm = max(bbox.YLength, 1e-3)
        scale_pt_per_mm = min(usable_w_pt / width_mm, usable_h_pt / height_mm)
        # Respect the requested scale hint by adjusting the drawing scale factor.
        if scale_hint > 0:
            scale_pt_per_mm /= scale_hint

        content_w_pt = width_mm * scale_pt_per_mm
        content_h_pt = height_mm * scale_pt_per_mm
        offset_x_pt = mm_to_pt(margin_side_mm) + max(0.0, (usable_w_pt - content_w_pt) / 2.0)
        offset_y_pt = (
            mm_to_pt(margin_top_mm)
            + max(0.0, (usable_h_pt - content_h_pt) / 2.0)
            + bbox.YMax * scale_pt_per_mm
        )
        translate_x = offset_x_pt - bbox.XMin * scale_pt_per_mm
        translate_y = offset_y_pt

        printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
        printer.setOutputFormat(QtPrintSupport.QPrinter.PdfFormat)
        printer.setOutputFileName(out_pdf_path)
        printer.setResolution(300)
        printer.setPageMargins(
            margin_side_mm,
            margin_top_mm,
            margin_side_mm,
            margin_bottom_mm,
            QtPrintSupport.QPrinter.Millimeter,
        )
        printer.setPageSizeMM(QtCore.QSizeF(page_w_mm, page_h_mm))

        painter = QtGui.QPainter(printer)
        if not painter.isActive():  # pragma: no cover - depends on runtime env
            raise RuntimeError(f"Could not activate PDF painter for {out_pdf_path}")
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        pen = QtGui.QPen(QtCore.Qt.black)
        pen.setWidthF(0.2)
        painter.setPen(pen)

        transform = QtGui.QTransform()
        transform.translate(translate_x, translate_y)
        transform.scale(scale_pt_per_mm, -scale_pt_per_mm)
        painter.setTransform(transform)

        try:
            for shape in shapes:
                for edge in shape.Edges:
                    pts = edge.discretize(Deflection=0.25)
                    if len(pts) < 2:
                        continue
                    path = QtGui.QPainterPath(QtCore.QPointF(pts[0].x, pts[0].y))
                    for pt in pts[1:]:
                        path.lineTo(pt.x, pt.y)
                    painter.drawPath(path)
        finally:
            painter.resetTransform()

            if with_titleblock:
                painter.setPen(QtGui.QPen(QtCore.Qt.black))
                font = QtGui.QFont()
                font.setPointSizeF(10.0)
                painter.setFont(font)
                title_rect = QtCore.QRectF(
                    mm_to_pt(margin_side_mm),
                    mm_to_pt(5.0),
                    page_w_pt - 2.0 * mm_to_pt(margin_side_mm),
                    mm_to_pt(margin_top_mm - 6.0),
                )
                painter.drawText(
                    title_rect,
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                    f"{title} (scale {scale_hint:.2f} : 1)",
                )

            painter.end()

        print(f"[INFO] Qt PDF fallback wrote {out_pdf_path}")

    backend = pdf_backend.lower()
    if backend not in PDF_BACKENDS:
        raise ValueError(f"Unsupported PDF backend '{pdf_backend}'")

    if backend == "techdraw":
        if not TECHDRAW_AVAILABLE:
            raise RuntimeError(
                "TechDraw backend requested but TechDraw module is unavailable."
            )
        _techdraw_pdf()
        return

    if backend == "qt":
        _qt_pdf_fallback()
        return

    # backend == "auto"
    if TECHDRAW_AVAILABLE:
        try:
            _techdraw_pdf()
            return
        except Exception as exc:
            print(f"[WARN] TechDraw export failed ({exc}); falling back to Qt.")

    try:
        _qt_pdf_fallback()
    except Exception as exc:  # pragma: no cover - depends on runtime env
        print(f"[WARN] TechDraw unavailable and PDF fallback failed: {exc}")


# -----------------------------
# Argument parsing & scaling
# -----------------------------
def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate 1:1 technical drawings (DXF/SVG/PDF) for a mini wooden wheelbarrow "
            "with FreeCAD."
        )
    )
    parser.add_argument("--out", dest="outdir", default="./out", help="Output directory")
    parser.add_argument(
        "--paper",
        dest="paper",
        default="A4",
        choices=list(PAPER_SIZES_MM.keys()),
        help="Paper size for PDF output (TechDraw)",
    )
    parser.add_argument(
        "--scale",
        dest="scale",
        type=float,
        default=1.0,
        help="Global uniform scale (1.0 = full size). DXF/SVG remain 1:1; the PDF is scaled.",
    )
    parser.add_argument(
        "--title",
        dest="title",
        default="Mini Wheelbarrow — Full-Scale Drawings",
        help="Title for the PDF sheet",
    )
    parser.add_argument(
        "--no-titleblock",
        dest="no_titleblock",
        action="store_true",
        help="Generate the PDF without a title block (exact paper size)",
    )
    parser.add_argument(
        "--pdf-backend",
        dest="pdf_backend",
        default="techdraw",
        choices=PDF_BACKENDS,
        help=(
            "PDF export backend to use: 'techdraw' (default, requires TechDraw), "
            "'qt' (PySide fallback), or 'auto' (TechDraw when available, otherwise Qt)."
        ),
    )
    parser.add_argument(
        "--validate",
        dest="validate",
        action="store_true",
        help="Measure key geometries and assert dimensions within tolerance.",
    )
    parser.add_argument(
        "--pdf-per-part",
        dest="pdf_per_part",
        action="store_true",
        help="Also generate one PDF per part group.",
    )
    parser.add_argument(
        "--tile-a4",
        dest="tile_a4",
        action="store_true",
        help="Split all_parts.svg into overlapping A4 tiles for household printing.",
    )
    return parser.parse_args(argv)


def scaled_params(scale: float) -> Dict[str, float]:
    return {key: value * scale for key, value in DEFAULT_PARAMS.items()}


# -----------------------------
# Main
# -----------------------------
def _strip_freecad_sentinel(argv: Sequence[str]) -> List[str]:
    """Remove the leading ``--`` emitted by ``freecadcmd`` when passing script args."""

    args = list(argv)
    if args and args[0] == "--":
        return args[1:]
    return args


def main(argv: List[str] | None = None) -> int:
    raw_args = list(argv if argv is not None else sys.argv[1:])
    args = parse_args(_strip_freecad_sentinel(raw_args))

    if args.scale <= 0:
        raise ValueError("Scale must be positive.")

    export_prefs.configure()

    ensure_dir(args.outdir)

    params = scaled_params(args.scale)

    if DOC_NAME in App.listDocuments():
        doc = App.getDocument(DOC_NAME)
    else:
        doc = App.newDocument(DOC_NAME)

    groups = layout_parts(doc, params, scale=args.scale)

    if args.validate:
        validate_geometry(doc, params)
        print("[OK] Validation passed.")

    for key, objs in groups.items():
        export_group(objs, os.path.join(args.outdir, key))

    all_objs: List[App.DocumentObject] = []
    for objs in groups.values():
        all_objs.extend(objs)
    export_group(all_objs, os.path.join(args.outdir, "all_parts"))

    if args.tile_a4:
        svg_path = os.path.join(args.outdir, "all_parts.svg")
        if os.path.exists(svg_path):
            tile_svg_to_a4(
                svg_path,
                os.path.join(args.outdir, "tiles_a4"),
                PAPER_SIZES_MM["A4"],
            )
        else:
            print(f"[WARN] Cannot tile SVG; {svg_path} not found.")

    pdf_path = os.path.join(args.outdir, "all_parts.pdf")
    make_pdf_page_from_objects(
        doc,
        all_objs,
        paper=args.paper,
        title=args.title,
        out_pdf_path=pdf_path,
        with_titleblock=(not args.no_titleblock),
        scale_hint=args.scale,
        pdf_backend=args.pdf_backend,
    )

    if args.pdf_per_part:
        for key, objs in groups.items():
            make_pdf_page_from_objects(
                doc,
                objs,
                paper=args.paper,
                title=f"{args.title} — {key}",
                out_pdf_path=os.path.join(args.outdir, f"{key}.pdf"),
                with_titleblock=(not args.no_titleblock),
                scale_hint=args.scale,
                pdf_backend=args.pdf_backend,
            )

    doc.saveAs(os.path.join(args.outdir, "freecad_source.FCStd"))
    print(f"[OK] Exports in: {args.outdir}")
    if os.path.exists(pdf_path):
        print(f"[OK] PDF ({args.pdf_backend} backend): {pdf_path}")
    else:
        if args.pdf_backend == "techdraw":
            print("[WARN] TechDraw was available but PDF export did not create a file.")
        elif args.pdf_backend == "qt":
            print("[WARN] Qt PDF backend requested but no file was generated.")
        elif TECHDRAW_AVAILABLE:
            print(
                "[WARN] TechDraw was available but auto PDF export did not create a file."
            )
        else:
            print("[INFO] TechDraw unavailable; PDF fallback skipped or failed.")
    return 0


if __name__ == "__main__":  # pragma: no cover - FreeCADCmd entry point
    raise SystemExit(main())


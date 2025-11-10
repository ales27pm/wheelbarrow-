#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate 1:1 fabrication drawings for the mini wooden wheelbarrow.

This module reproduces the headless export pipeline that previously lived only
inside the `MiniWheelbarrow.FCMacro` macro. Running it with ``FreeCADCmd``
creates 2D profiles for every part (rails, tray panels, spreaders, legs, axle
block, and wheel) and exports them as DXF/SVG files. When the TechDraw
workbench is available a consolidated PDF sheet is produced as well.

The script defaults to generating the original 1:1 drawings, but all linear
dimensions can be scaled uniformly via ``--scale``. Scaling affects every
numerical parameter as well as the layout spacing, ensuring that the exported
files remain non-overlapping at any size.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Dict, Iterable, List, Sequence, Tuple

# --- FreeCAD / Workbenches ---
import FreeCAD as App
import Draft
import Import

try:  # TechDraw is optional (only required for PDF generation)
    import TechDraw

    TECHDRAW_AVAILABLE = True
except Exception:  # pragma: no cover - FreeCADCmd without TechDraw hits here
    TECHDRAW_AVAILABLE = False

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


def recompute(doc: App.Document) -> None:
    try:
        doc.recompute()
    except Exception as exc:
        print(f"[WARN] Document recompute failed: {exc}")


def add_text(doc: App.Document, text: str, pos: Vector2D, *, size: float = 4.0) -> App.DocumentObject:
    annotation = Draft.make_text([text], point=App.Vector(pos[0], pos[1], 0))
    annotation.ViewObject.FontSize = size
    return annotation


def add_linear_dimension(doc: App.Document, p1: Vector2D, p2: Vector2D, dim_pos: Vector2D) -> App.DocumentObject:
    v1 = App.Vector(p1[0], p1[1], 0)
    v2 = App.Vector(p2[0], p2[1], 0)
    vdim = App.Vector(dim_pos[0], dim_pos[1], 0)
    dim = Draft.make_dimension(v1, v2, vdim)
    dim.ViewObject.FontSize = 3.0
    dim.ViewObject.ArrowSize = 2.0
    dim.ViewObject.ExtLines = 1
    dim.ViewObject.ShowUnit = False
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

    ``Import.export`` works for many formats but FreeCAD's AppImage builds ship the
    dedicated ``importDXF``/``importSVG`` modules that offer better reliability for
    2D outputs.  Falling back to ``Import.export`` keeps compatibility with older
    installs while still surfacing any failure as a hard error so CI can flag it
    immediately instead of silently producing empty artifact directories.
    """

    objs = list(objects)
    if not objs:
        raise ValueError(f"No objects provided for export to {out_path_base}")

    dxf_path = f"{out_path_base}.dxf"
    svg_path = f"{out_path_base}.svg"

    errors: List[str] = []

    # --- DXF ---
    try:
        import importDXF  # type: ignore[import-not-found]

        importDXF.export(objs, dxf_path)
    except Exception as exc:  # pragma: no cover - depends on FreeCAD environment
        try:
            Import.export(objs, dxf_path)
        except Exception as fallback_exc:  # pragma: no cover - env specific
            errors.append(f"DXF export failed for {out_path_base}: {fallback_exc}")
            print(f"[WARN] DXF export failed for {out_path_base}: {fallback_exc}")
        else:
            print(
                "[WARN] importDXF unavailable; exported DXF via Import.export()."
            )
    if not os.path.exists(dxf_path):
        errors.append(f"DXF export did not create {dxf_path}")

    # --- SVG ---
    try:
        import importSVG  # type: ignore[import-not-found]

        importSVG.export(objs, svg_path)
    except Exception as exc:  # pragma: no cover - depends on FreeCAD environment
        try:
            Import.export(objs, svg_path)
        except Exception as fallback_exc:  # pragma: no cover - env specific
            errors.append(f"SVG export failed for {out_path_base}: {fallback_exc}")
            print(f"[WARN] SVG export failed for {out_path_base}: {fallback_exc}")
        else:
            print(
                "[WARN] importSVG unavailable; exported SVG via Import.export()."
            )
    if not os.path.exists(svg_path):
        errors.append(f"SVG export did not create {svg_path}")

    if errors:
        raise RuntimeError("; ".join(errors))


def make_pdf_page_from_objects(
    doc: App.Document,
    objects: Iterable[App.DocumentObject],
    *,
    paper: str = "A4",
    title: str = "Sheet",
    out_pdf_path: str | None = None,
    with_titleblock: bool = True,
) -> None:
    if not TECHDRAW_AVAILABLE:
        print("[INFO] TechDraw not available: PDF not generated.")
        return

    page = doc.addObject("TechDraw::DrawPage", f"{title}_Page")
    if with_titleblock:
        template = doc.addObject("TechDraw::DrawSVGTemplate", f"{title}_Template")
        template.Template = TechDraw.getStandardTemplate("A4_LandscapeTD.svg")
        page.Template = template
    else:
        w, h = PAPER_SIZES_MM.get(paper, PAPER_SIZES_MM[DEFAULT_PAPER])
        page.Width = w
        page.Height = h

    for i, obj in enumerate(objects):
        view = doc.addObject("TechDraw::DrawViewPart", f"{title}_View_{i + 1}")
        view.Source = [obj]
        page.addView(view)

    recompute(doc)

    if out_pdf_path:
        try:
            page.exportPageAsPdf(out_pdf_path)
        except Exception as exc:  # pragma: no cover - depends on TechDraw
            print(f"[WARN] PDF export failed ({out_pdf_path}): {exc}")


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
    return parser.parse_args(argv)


def scaled_params(scale: float) -> Dict[str, float]:
    return {key: value * scale for key, value in DEFAULT_PARAMS.items()}


# -----------------------------
# Main
# -----------------------------
def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    if args.scale <= 0:
        raise ValueError("Scale must be positive.")

    ensure_dir(args.outdir)

    params = scaled_params(args.scale)

    if DOC_NAME in App.listDocuments():
        doc = App.getDocument(DOC_NAME)
    else:
        doc = App.newDocument(DOC_NAME)

    groups = layout_parts(doc, params, scale=args.scale)

    for key, objs in groups.items():
        export_group(objs, os.path.join(args.outdir, key))

    all_objs: List[App.DocumentObject] = []
    for objs in groups.values():
        all_objs.extend(objs)
    export_group(all_objs, os.path.join(args.outdir, "all_parts"))

    pdf_path = os.path.join(args.outdir, "all_parts.pdf")
    make_pdf_page_from_objects(
        doc,
        all_objs,
        paper=args.paper,
        title=args.title,
        out_pdf_path=pdf_path,
        with_titleblock=(not args.no_titleblock),
    )

    doc.saveAs(os.path.join(args.outdir, "freecad_source.FCStd"))
    print(f"[OK] Exports in: {args.outdir}")
    if TECHDRAW_AVAILABLE:
        print(f"[OK] PDF: {pdf_path}")
    else:
        print("[INFO] TechDraw not available; PDF skipped.")
    return 0


if __name__ == "__main__":  # pragma: no cover - FreeCADCmd entry point
    raise SystemExit(main())


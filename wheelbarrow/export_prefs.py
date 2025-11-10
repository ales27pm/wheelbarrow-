"""Helpers for configuring FreeCAD export preferences."""

from __future__ import annotations

import FreeCAD as App


def configure() -> None:
    """Force known-good DXF/SVG/unit preferences for deterministic exports."""

    # Units in millimetres
    App.ParamGet("User parameter:BaseApp/Preferences/Units").SetInt("UserSchema", 0)

    pref = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Import")
    pref.SetBool("UseLegacyDXFImporter", False)
    pref.SetBool("UseLegacyDXFExporter", False)
    pref.SetBool("ExportSplines", True)
    pref.SetBool("DXFUseScaling", False)
    pref.SetString("DXFTextStyle", "STANDARD")
    pref.SetInt("DXFDecimalPlaces", 3)
    pref.SetFloat("SvgStrokeWidth", 0.2)
    pref.SetBool("SvgExportTextAsPaths", False)

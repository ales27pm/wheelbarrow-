# Mini Wheelbarrow FreeCAD Macro

This repository contains a FreeCAD macro that generates a fully parametric mini wooden wheelbarrow model. Running the macro from FreeCAD 0.20+ produces:

- Individual 3D solids for each part (rails, tray panels, spreaders, legs, axle block, and wheel)
- 1:1 DXF and SVG exports for every component
- Optional TechDraw sheets (if the TechDraw workbench is available)
- A final compound assembly model of the wheelbarrow

## Getting Started

1. Copy [`macros/MiniWheelbarrow.FCMacro`](macros/MiniWheelbarrow.FCMacro) into your FreeCAD macro directory or open it directly in the FreeCAD macro editor.
2. Launch FreeCAD 0.20 or newer and run the macro.
3. The macro creates/updates a document named `MiniWheelbarrow` and exports all drawings to `App.getUserAppDataDir()/Wheelbarrow_Drawings` (e.g., `~/.local/share/FreeCAD/Wheelbarrow_Drawings` on Linux). Set the `WHEELBARROW_EXPORT_DIR` environment variable to override the export location.

### Headless execution (FreeCADCmd)

The macro can be executed without the GUI for automated exports:

```bash
# Download an official FreeCAD AppImage (example for 1.0.2) and extract it
wget https://github.com/FreeCAD/FreeCAD/releases/download/1.0.2/FreeCAD_1.0.2-conda-Linux-x86_64-py311.AppImage -O FreeCAD.AppImage
chmod +x FreeCAD.AppImage
./FreeCAD.AppImage --appimage-extract

# Run the macro with the bundled FreeCADCmd binary
WHEELBARROW_EXPORT_DIR="$PWD/wheelbarrow-exports" ./squashfs-root/usr/bin/freecadcmd macros/MiniWheelbarrow.FCMacro
```

DXF/SVG exports are produced in the directory printed at the end of the run. TechDraw PDFs are generated when the build provides `TechDraw` with PDF export support.

### Automated artifact builds

Every push, pull request, or manual dispatch triggers the **Build wheelbarrow fabrication artifacts** GitHub Actions workflow. The pipeline downloads the official FreeCAD 1.0.2 AppImage, runs the macro headlessly with `freecadcmd`, and uploads the generated DXF, SVG, FCStd, and (when available) TechDraw PDF files as a downloadable workflow artifact. Navigate to the workflow run in the Actions tab and download the `wheelbarrow-fabrication-assets` bundle to retrieve the latest fabrication-ready outputs (both as individual files under `raw/` and as a single `wheelbarrow-fabrication.tar.gz` archive).

## Notes

- If the TechDraw workbench is not available (e.g., when running headless) the macro skips drawing page generation but still exports the DXF/SVG profiles and saves the FCStd model.
- All key dimensions are defined in the `P` dictionary near the top of the macro and can be adjusted for different proportions.

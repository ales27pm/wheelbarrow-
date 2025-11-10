#!/usr/bin/env bash
# shellcheck shell=bash

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    set -euo pipefail
fi

SCRIPT_DIR=$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
THIRD_PARTY_DIR="${REPO_DIR}/third_party"

ensure_third_party_dir() {
    mkdir -p "${THIRD_PARTY_DIR}"
}

# shellcheck disable=SC2120
freecad_python_env() {
    ensure_third_party_dir

    local appimage_dir="${FREECAD_APPIMAGE_DIR:-}"
    local python_bin
    local python_path_hint="${FREECAD_PYTHONPATH:-}"

    if [ -z "${appimage_dir}" ]; then
        echo "FREECAD_APPIMAGE_DIR must be set to locate the bundled Python runtime" >&2
        return 1
    fi

    python_bin="${FREECAD_PYTHON:-${appimage_dir}/squashfs-root/usr/bin/python}"
    if [ ! -x "${python_bin}" ]; then
        echo "FreeCAD Python interpreter not found or not executable: ${python_bin}" >&2
        return 1
    fi

    if [ -z "${python_path_hint}" ]; then
        for candidate in "${appimage_dir}"/squashfs-root/usr/lib/python3.*; do
            if [ -d "${candidate}" ]; then
                python_path_hint="${candidate}/site-packages:${appimage_dir}/squashfs-root/usr/lib"
                break
            fi
        done
    fi

    if [ -z "${python_path_hint}" ]; then
        echo "Unable to determine FreeCAD AppImage Python site-packages path" >&2
        return 1
    fi

    export FREECAD_PYTHON_BIN="${python_bin}"
    export FREECAD_PYTHONPATH_RESOLVED="${python_path_hint}:${THIRD_PARTY_DIR}"
}

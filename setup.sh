#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FREECAD_APPIMAGE_VERSION="1.0.2"
FREECAD_CACHE_DIR="${REPO_DIR}/.freecad"
FREECAD_APPIMAGE_DIR="${FREECAD_CACHE_DIR}/appimage-${FREECAD_APPIMAGE_VERSION}"
FREECAD_APPIMAGE_PATH="${FREECAD_APPIMAGE_DIR}/FreeCAD_${FREECAD_APPIMAGE_VERSION}.AppImage"
FREECAD_APPIMAGE_CMD_PATH="${FREECAD_APPIMAGE_DIR}/squashfs-root/usr/bin/freecadcmd"
FREECAD_APPIMAGE_URL="https://github.com/FreeCAD/FreeCAD/releases/download/${FREECAD_APPIMAGE_VERSION}/FreeCAD_${FREECAD_APPIMAGE_VERSION}-conda-Linux-x86_64-py311.AppImage"
APPIMAGE_RUNTIME_DEPS=(
    fonts-dejavu-core
    libegl1
    libgl1
    libglu1-mesa
    libxkbcommon-x11-0
    libxrender1
    libxcb-icccm4
    libxcb-image0
    libxcb-keysyms1
    libxcb-randr0
    libxcb-render-util0
    libxcb-shape0
    libxcb-xinerama0
    libxcb-xkb1
    xz-utils
)

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
error() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*"; exit 1; }

need_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        error "Required command '$1' not found. Install it manually and re-run."
    fi
}

install_with_apt() {
    info "Installing Python runtime prerequisites with apt-get"
    need_cmd sudo
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip
}

install_with_dnf() {
    info "Installing Python runtime prerequisites with dnf"
    need_cmd sudo
    sudo dnf install -y python3 python3-pip
}

install_with_pacman() {
    info "Installing Python runtime prerequisites with pacman"
    need_cmd sudo
    sudo pacman -Sy --noconfirm python python-pip
}

install_with_brew() {
    info "Installing FreeCAD and Python runtime with Homebrew"
    need_cmd brew
    brew update
    brew install --cask freecad
    brew install python@3.12
}

install_python_helpers() {
    info "Installing shared Python helpers (defusedxml)"
    python3 -m ensurepip --upgrade >/dev/null 2>&1 || true
    python3 -m pip install --upgrade --target "${REPO_DIR}/third_party" defusedxml
}

install_appimage_runtime_deps() {
    if command -v apt-get >/dev/null 2>&1; then
        info "Installing FreeCAD AppImage runtime dependencies with apt-get"
        need_cmd sudo
        sudo apt-get update
        sudo apt-get install -y "${APPIMAGE_RUNTIME_DEPS[@]}"
    else
        warn "AppImage runtime dependency installation is only automated for apt-get; please install equivalents manually."
    fi
}

prepare_appimage_with_runtime() {
    install_appimage_runtime_deps
    prepare_appimage
    if [ ! -x "${FREECAD_APPIMAGE_CMD_PATH}" ]; then
        warn "FreeCADCmd from the AppImage is not executable at ${FREECAD_APPIMAGE_CMD_PATH}."
    fi
}

prepare_appimage() {
    need_cmd curl
    need_cmd chmod
    mkdir -p "${FREECAD_APPIMAGE_DIR}"
    if [ ! -f "${FREECAD_APPIMAGE_PATH}" ]; then
        info "Downloading FreeCAD AppImage ${FREECAD_APPIMAGE_VERSION}"
        curl -L "${FREECAD_APPIMAGE_URL}" -o "${FREECAD_APPIMAGE_PATH}"
        chmod +x "${FREECAD_APPIMAGE_PATH}"
    else
        info "Reusing cached FreeCAD AppImage at ${FREECAD_APPIMAGE_PATH}"
    fi

    if [ ! -d "${FREECAD_APPIMAGE_DIR}/squashfs-root" ]; then
        info "Extracting FreeCAD AppImage squashfs payload"
        (cd "${FREECAD_APPIMAGE_DIR}" && "${FREECAD_APPIMAGE_PATH}" --appimage-extract >/dev/null 2>&1)
    fi

    local python_bin="${FREECAD_APPIMAGE_DIR}/squashfs-root/usr/bin/python"
    local python_path_hint=""
    for candidate in "${FREECAD_APPIMAGE_DIR}"/squashfs-root/usr/lib/python3.*; do
        if [ -d "${candidate}" ]; then
            python_path_hint="${candidate}/site-packages:${FREECAD_APPIMAGE_DIR}/squashfs-root/usr/lib"
            break
        fi
    done

    info "AppImage FreeCADCmd available at ${FREECAD_APPIMAGE_DIR}/squashfs-root/usr/bin/freecadcmd"
    if [ -x "${python_bin}" ]; then
        info "AppImage Python available at ${python_bin}"
    fi
}

main() {
    if [[ $# -gt 0 ]]; then
        case "$1" in
            --prepare-appimage)
                install_appimage_runtime_deps
                prepare_appimage
                install_python_helpers
                if [[ -n "${GITHUB_ENV:-}" ]]; then
                    {
                        echo "FREECAD_APPIMAGE_DIR=${FREECAD_APPIMAGE_DIR}"
                        echo "FREECADCMD_PATH=${FREECAD_APPIMAGE_DIR}/squashfs-root/usr/bin/freecadcmd"
                        if [ -x "${FREECAD_APPIMAGE_DIR}/squashfs-root/usr/bin/python" ]; then
                            echo "FREECAD_PYTHON=${FREECAD_APPIMAGE_DIR}/squashfs-root/usr/bin/python"
                        fi
                        python_path_hint=""
                        for candidate in "${FREECAD_APPIMAGE_DIR}"/squashfs-root/usr/lib/python3.*; do
                            if [ -d "${candidate}" ]; then
                                python_path_hint="${candidate}/site-packages:${FREECAD_APPIMAGE_DIR}/squashfs-root/usr/lib"
                                break
                            fi
                        done
                        if [ -n "${python_path_hint}" ]; then
                            echo "FREECAD_PYTHONPATH=${python_path_hint}"
                        fi
                    } >>"${GITHUB_ENV}"
                fi
                info "FreeCAD AppImage ready at ${FREECAD_APPIMAGE_DIR}"
                return 0
                ;;
            -h|--help)
                cat <<USAGE
Usage: ./setup.sh [--prepare-appimage]

Without arguments the script installs FreeCAD (or the AppImage fallback) and
creates a Python virtual environment for local helpers.

  --prepare-appimage   Install minimal AppImage runtime dependencies and
                       download/extract the cached FreeCAD AppImage. Intended
                       for CI usage.
USAGE
                return 0
                ;;
        esac
    fi

    freecadcmd_path=""
    if command -v freecadcmd >/dev/null 2>&1; then
        freecadcmd_path="$(command -v freecadcmd)"
        info "FreeCADCmd already available: ${freecadcmd_path}"
    else
        case "$(uname -s)" in
            Linux)
                if command -v apt-get >/dev/null 2>&1; then
                    install_with_apt
                elif command -v dnf >/dev/null 2>&1; then
                    install_with_dnf
                elif command -v pacman >/dev/null 2>&1; then
                    install_with_pacman
                else
                    warn "No supported package manager detected; attempting AppImage setup without additional prerequisites."
                fi

                info "Preparing FreeCAD AppImage from GitHub release"
                prepare_appimage_with_runtime
                freecadcmd_path="${FREECAD_APPIMAGE_CMD_PATH}"
                ;;
            Darwin)
                install_with_brew
                ;;
            *)
                warn "Unsupported OS $(uname -s); preparing AppImage fallback."
                prepare_appimage
                freecadcmd_path="${FREECAD_APPIMAGE_DIR}/squashfs-root/usr/bin/freecadcmd"
                ;;
        esac
        if [ -z "${freecadcmd_path}" ] && command -v freecadcmd >/dev/null 2>&1; then
            freecadcmd_path="$(command -v freecadcmd)"
        fi
    fi

    if [ -z "${freecadcmd_path}" ]; then
        warn "FreeCADCmd not found on PATH. Use the AppImage fallback instructions printed below."
    fi

    if ! command -v python3 >/dev/null 2>&1; then
        error "python3 is required but could not be installed automatically."
    fi

    install_python_helpers

    info "Creating Python virtual environment for FreeCAD helpers"
    python3 -m venv "${REPO_DIR}/.venv"
    "${REPO_DIR}/.venv/bin/pip" install --upgrade pip wheel

    cat <<SETUP_NOTE

Setup complete.

If FreeCADCmd is on PATH (${freecadcmd_path:-unavailable}), activate the helper environment and run:

  source "${REPO_DIR}/.venv/bin/activate"
  freecadcmd --console --python "${REPO_DIR}/generate_wheelbarrow_drawings.py" -- --out ./plans

  (The ``--`` sentinel is required when invoking via FreeCADCmd so script arguments
  pass through correctly. It should be omitted when calling the script with Python
  directly.)

For the AppImage fallback, ensure the environment variables are set before running:

  source "${REPO_DIR}/.venv/bin/activate"
  export FREECAD_APPIMAGE_DIR="${FREECAD_APPIMAGE_DIR}"
  export FREECADPATH="${FREECAD_APPIMAGE_DIR}/squashfs-root/usr/lib/freecad/lib"
  export LD_LIBRARY_PATH="${FREECAD_APPIMAGE_DIR}/squashfs-root/usr/lib:${LD_LIBRARY_PATH:-}"
  source "${REPO_DIR}/scripts/freecad_python_env.sh"
  freecad_python_env
  export PYTHONPATH="${FREECAD_PYTHONPATH_RESOLVED}"
  "${FREECAD_PYTHON_BIN}" "${REPO_DIR}/generate_wheelbarrow_drawings.py" --out ./plans

SETUP_NOTE
}

main "$@"

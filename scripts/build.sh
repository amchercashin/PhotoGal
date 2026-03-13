#!/usr/bin/env bash
set -euo pipefail

# PhotoGal macOS build script
# Builds .dmg from source (Apple Silicon only)
# Usage: ./scripts/build.sh

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
FRONTEND="$REPO_ROOT/frontend"
TAURI="$REPO_ROOT/src-tauri"
CARGO_BIN="$HOME/.cargo/bin"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[build]${NC} $1"; }
warn()  { echo -e "${YELLOW}[build]${NC} $1"; }
error() { echo -e "${RED}[build]${NC} $1" >&2; }

# --- 1. Validate environment ---
info "Checking environment..."

MISSING=""
[ ! -d "$BACKEND/.venv" ] && MISSING="$MISSING backend/.venv"
[ ! -d "$FRONTEND/node_modules" ] && MISSING="$MISSING frontend/node_modules"
[ ! -f "$CARGO_BIN/cargo" ] && MISSING="$MISSING cargo"
command -v npm &>/dev/null || MISSING="$MISSING npm"

if [ -n "$MISSING" ]; then
    error "Missing dependencies:$MISSING"
    echo "Install:"
    echo "  backend/.venv    → cd backend && uv venv && uv pip install -e '.[dev]'"
    echo "  node_modules     → cd frontend && npm install"
    echo "  cargo            → curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
fi

# Check PyInstaller available in venv
if ! "$BACKEND/.venv/bin/python" -c "import PyInstaller" 2>/dev/null; then
    error "PyInstaller not installed in backend/.venv"
    echo "Install: cd backend && .venv/bin/pip install pyinstaller"
    exit 1
fi

# Check Tauri CLI
if ! "$CARGO_BIN/cargo" tauri --version &>/dev/null; then
    error "Tauri CLI not installed"
    echo "Install: cargo install tauri-cli"
    exit 1
fi

info "Environment OK"

# --- 2. Build Python sidecar ---
info "Building Python sidecar (PyInstaller)..."
cd "$BACKEND"
.venv/bin/python -m PyInstaller photogal-server.spec --clean --noconfirm
SIDECAR="$BACKEND/dist/photogal-server"

if [ ! -f "$SIDECAR" ]; then
    error "PyInstaller failed — $SIDECAR not found"
    exit 2
fi
SIDECAR_SIZE=$(du -sh "$SIDECAR" | cut -f1)
info "Sidecar built: $SIDECAR ($SIDECAR_SIZE)"

# --- 3. Copy sidecar to Tauri binaries ---
info "Copying sidecar to Tauri binaries..."
mkdir -p "$TAURI/binaries"
cp "$SIDECAR" "$TAURI/binaries/photogal-server-aarch64-apple-darwin"

# --- 4. Build frontend ---
info "Building frontend..."
cd "$FRONTEND"
npm run build

if [ ! -d "$FRONTEND/dist" ]; then
    error "Frontend build failed — dist/ not found"
    exit 2
fi
info "Frontend built"

# --- 5. Build Tauri app ---
info "Building Tauri app..."
cd "$REPO_ROOT"
"$CARGO_BIN/cargo" tauri build

# --- 6. Find and report .dmg ---
VERSION=$(grep '"version"' "$TAURI/tauri.conf.json" | head -1 | sed 's/.*"version": *"\([^"]*\)".*/\1/')
if [ -z "$VERSION" ]; then
    error "Failed to extract version from tauri.conf.json"
    exit 2
fi
DMG="$TAURI/target/release/bundle/dmg/PhotoGal_${VERSION}_aarch64.dmg"

if [ ! -f "$DMG" ]; then
    # Try to find any .dmg
    DMG=$(find "$TAURI/target/release/bundle/dmg" -name "*.dmg" -type f 2>/dev/null | head -1)
fi

if [ -f "$DMG" ]; then
    DMG_SIZE=$(du -sh "$DMG" | cut -f1)
    info "Build complete!"
    info "DMG: $DMG ($DMG_SIZE)"
    echo "$DMG"
else
    error "Build completed but .dmg not found"
    exit 2
fi

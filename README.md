# PhotoGal

Desktop app (macOS) for organizing large photo libraries (100k+ photos) with AI-powered classification.

<!-- TODO: add screenshot -->

## Features

- **Progressive processing pipeline** — three-level analysis from fast scan to deep AI
  - **L0 — Scan:** file hashing, EXIF extraction, thumbnails, duplicate detection
  - **L1 — Analysis:** blur/exposure quality scoring, smart clustering, geocoding
  - **L2 — AI:** CLIP embeddings, 22-category classification, cluster merging, ranking
- **CLIP-powered search** — find photos by text description (supports Russian via offline translation)
- **Face detection & grouping** — automatic face detection with person-based browsing
- **GPU acceleration** — CUDA, MPS (Apple Silicon), and CPU auto-detection
- **Fully offline** — no cloud, no accounts, all processing runs locally
- **Duplicate detection** — SHA256-based exact duplicate finding

## Architecture

```
Tauri 2.x (Rust) → WebView (React 19 + Vite + Tailwind) → HTTP → FastAPI (Python 3.12) → SQLite (WAL)
```

The Python backend runs as a sidecar process bundled via PyInstaller. The Rust shell manages the sidecar lifecycle, and the React frontend communicates with it over localhost HTTP.

## Download

Pre-built macOS (Apple Silicon) `.dmg` available on the [Releases](https://github.com/amchercashin/PhotoGal/releases) page.

## Build from Source

### Prerequisites

- macOS with Apple Silicon
- Python 3.12 + [uv](https://github.com/astral-sh/uv)
- Node.js 18+ and npm
- Rust toolchain ([rustup](https://rustup.rs/))
- Tauri CLI: `cargo install tauri-cli`

### Setup

```bash
# Backend
cd backend
uv venv && uv pip install -e '.[dev]'
.venv/bin/pip install pyinstaller

# Frontend
cd ../frontend
npm install
```

### Build

```bash
./scripts/build.sh
```

This builds the PyInstaller sidecar, frontend, and Tauri app. Output: `src-tauri/target/release/bundle/dmg/PhotoGal_<version>_aarch64.dmg`

### Development

```bash
# Backend (from /backend)
.venv/bin/python -m photogal.cli serve --port 8765

# Frontend (from /frontend)
npm run dev  # http://localhost:5173

# Tests
cd backend && .venv/bin/python -m pytest tests/ -v
```

## Tech Stack

| Layer | Technology |
|---|---|
| Desktop | Tauri 2.x (Rust) |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS |
| Backend | Python 3.12, FastAPI, SQLite (WAL) |
| AI | open-clip-torch (ViT-L-14), scikit-learn |
| Image | Pillow + pillow-heif, imagehash, exifread |
| Geo | reverse-geocoder (offline) |
| Bundling | PyInstaller (sidecar) |

## License

[MIT](LICENSE)

# PhotoGal Build & Release Scripts

## Prerequisites

- macOS with Apple Silicon (aarch64)
- Python 3.12 with venv: `cd backend && uv venv && uv pip install -e '.[dev]'`
- PyInstaller: `cd backend && .venv/bin/pip install pyinstaller`
- Node.js + npm: `cd frontend && npm install`
- Rust toolchain: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
- Tauri CLI: `cargo install tauri-cli`
- GitHub CLI: `brew install gh && gh auth login`

## scripts/build.sh

Builds PhotoGal .dmg from source. Run independently to test the build.

```bash
./scripts/build.sh
```

Steps: PyInstaller sidecar → copy to Tauri binaries → npm build → cargo tauri build → .dmg

Output: `src-tauri/target/release/bundle/dmg/PhotoGal_<version>_aarch64.dmg`

## scripts/release.sh

Full release cycle: version bump → build → tag → push → GitHub Release.

```bash
./scripts/release.sh 0.2.0
```

Steps:
1. Validates: semver format, on `main`, clean tree, tag doesn't exist, `gh` authenticated
2. Bumps version in: `pyproject.toml`, `tauri.conf.json`, `Cargo.toml`, `package.json`
3. Calls `build.sh`
4. Verifies .dmg artifact exists
5. Commits version bump, tags, pushes
6. Creates GitHub Release with .dmg attached

If build fails, version changes are automatically reverted.

#!/usr/bin/env bash
set -euo pipefail

# PhotoGal release script
# Full cycle: version bump → build → tag → push → GitHub Release
# Usage: ./scripts/release.sh <version>
# Example: ./scripts/release.sh 0.2.0

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$REPO_ROOT/scripts"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[release]${NC} $1"; }
warn()  { echo -e "${YELLOW}[release]${NC} $1"; }
error() { echo -e "${RED}[release]${NC} $1" >&2; }
die()   { error "$1"; exit 1; }

# --- 1. Validate arguments ---
VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    echo "Usage: $0 <version>"
    echo "Example: $0 0.2.0"
    exit 1
fi

if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    die "Invalid version format: $VERSION (expected X.Y.Z)"
fi

TAG="v$VERSION"

# --- 2. Validate environment ---
info "Validating environment..."

BRANCH=$(git -C "$REPO_ROOT" branch --show-current)
[ "$BRANCH" != "main" ] && die "Must be on 'main' branch (currently on '$BRANCH')"

if ! git -C "$REPO_ROOT" diff --quiet || ! git -C "$REPO_ROOT" diff --cached --quiet; then
    die "Working tree is dirty — commit or stash changes first"
fi

if git -C "$REPO_ROOT" tag -l "$TAG" | grep -q "$TAG"; then
    die "Tag $TAG already exists"
fi

if ! gh auth status &>/dev/null; then
    die "gh CLI not authenticated — run 'gh auth login'"
fi

if ! git -C "$REPO_ROOT" remote get-url origin &>/dev/null; then
    die "No 'origin' remote configured"
fi

info "Environment OK (branch: $BRANCH, tag: $TAG)"

# --- 3. Bump version ---
info "Bumping version to $VERSION..."

# backend/pyproject.toml
sed -i '' "s/^version = \".*\"/version = \"$VERSION\"/" "$REPO_ROOT/backend/pyproject.toml"

# src-tauri/tauri.conf.json
sed -i '' "s/\"version\": \".*\"/\"version\": \"$VERSION\"/" "$REPO_ROOT/src-tauri/tauri.conf.json"

# src-tauri/Cargo.toml
sed -i '' 's/^version = ".*"/version = "'"$VERSION"'"/' "$REPO_ROOT/src-tauri/Cargo.toml"

# frontend/package.json
sed -i '' "s/\"version\": \".*\"/\"version\": \"$VERSION\"/" "$REPO_ROOT/frontend/package.json"

info "Version bumped in 4 files"

# --- 4. Build ---
info "Building..."
if ! "$SCRIPTS/build.sh"; then
    warn "Build failed — reverting version changes"
    git -C "$REPO_ROOT" checkout -- backend/pyproject.toml src-tauri/tauri.conf.json src-tauri/Cargo.toml frontend/package.json
    die "Build failed"
fi

# --- 5. Verify artifact ---
TAURI="$REPO_ROOT/src-tauri"
DMG="$TAURI/target/release/bundle/dmg/PhotoGal_${VERSION}_aarch64.dmg"

if [ ! -f "$DMG" ]; then
    DMG=$(find "$TAURI/target/release/bundle/dmg" -name "*.dmg" -type f 2>/dev/null | head -1)
fi

if [ ! -f "$DMG" ]; then
    warn "DMG not found — reverting version changes"
    git -C "$REPO_ROOT" checkout -- backend/pyproject.toml src-tauri/tauri.conf.json src-tauri/Cargo.toml frontend/package.json
    die ".dmg artifact not found after build"
fi

DMG_SIZE=$(du -sh "$DMG" | cut -f1)
info "Artifact: $DMG ($DMG_SIZE)"

# --- 6. Commit, tag, push ---
info "Committing version bump..."
cd "$REPO_ROOT"
git add backend/pyproject.toml src-tauri/tauri.conf.json src-tauri/Cargo.toml frontend/package.json
# Also add Cargo.lock if it changed (version bump updates it)
git add -f src-tauri/Cargo.lock 2>/dev/null || true
git commit -m "release: v$VERSION"

info "Tagging $TAG..."
git tag "$TAG"

info "Pushing to origin..."
git push origin main
git push origin "$TAG"

# --- 7. Create GitHub Release ---
info "Creating GitHub Release..."
gh release create "$TAG" "$DMG" \
    --title "PhotoGal $TAG" \
    --generate-notes

RELEASE_URL=$(gh release view "$TAG" --json url -q '.url')
info "Release created: $RELEASE_URL"
info "Done!"

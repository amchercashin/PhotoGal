# PhotoGal — Specification

Version: 0.2 | Date: 2026-03-11

## Purpose

Desktop application (macOS, Windows planned) for organizing large photo libraries (100k+ photos).
Sources: Apple Photos, Google Photos exports, or plain folder trees.

Goal: automatically classify and mark photos in progressive levels, then present them in a
convenient interface for review and action (delete, archive, create print albums).

---

## Processing Pipeline (Levels)

### Level 0 — Import & Scan
- User picks a folder via native file dialog
- App discovers all supported files recursively
- Each file: SHA256 hash, EXIF extraction, thumbnail generation (400px JPEG)
- Exact duplicate detection: photos with same content_hash → `dup` cluster, `is_exact_duplicate=1`
- Remaining photos → singleton clusters (each with `best_photo_id` set)
- Each photo inserted into DB with `processing_level=0`

**Supported formats:** .jpg .jpeg .png .heic .heif .tiff .tif .webp .bmp .raw .cr2 .nef .arw .dng

### Level 1 — Quality Analysis & Clustering
- **Phase 1** — Quality: blur detection (Laplacian variance), exposure analysis (mean brightness), pHash computation. Runs in parallel on thumbnails.
- **Phase 2** — Clustering: 4-group Union-Find algorithm:
  - **AB** (date + GPS available): pHash distance ≤ 12, time ≤ 3 min, GPS ≤ 50 m
  - **GPS** (GPS only, no date): pHash distance ≤ 6, GPS ≤ 50 m
  - **DATE** (date only, no GPS): pHash distance ≤ 12, time ≤ 3 min
  - **NONE** (neither): pHash distance ≤ 4
  - Groups NEVER cross-merge. Sliding-window optimization for time-sorted groups.
  - Incremental mode: new photos matched against existing L1 photos first.
- **Phase 3** — Geocoding: GPS → city/country/region via reverse-geocoder (offline, no API key)
- DB enriched: `quality_blur`, `quality_exposure`, `perceptual_hash`, `location_country`, `location_city`, `location_district`
- `processing_level=1`

### Level 2 — AI Semantic Layer (local model, no cloud)
- **Phase 4** — CLIP embeddings (ViT-L-14, laion2b_s32b_b82k) computed for all photos → `photo_embeddings` table
- **Phase 5** — 22-category zero-shot classification via prompt ensembling (3-5 prompts per category, averaged):
  - Content (14): portrait, selfie, group_photo, nature, architecture, monument, museum, food, animals, transport, interior, sports, event, book
  - Technical (8): screenshot, receipt, document, carsharing, meme, screen_photo, qr_code, reference
- **Phase 5.5** — CLIP merge: clusters with cosine similarity ≥ 0.90 (within 3 min + 50 m gate) merged
- **Phase 6** — Re-ranking within clusters by aesthetic score
- `processing_level=2`

**Aesthetic score:** `blur_norm * 0.6 + exposure_norm * 0.4`
- `blur_norm = min(blur / 500.0, 1.0)`
- `exposure_norm = max(0, 1.0 - abs(exposure - 128) / 128)`

### Level 3 — Events (FUTURE, not in v1)
- AI groups clusters into Events (trips, holidays, important dates)

### Level 4 — Print Albums (FUTURE, not in v1)
- Best-of-best selection within events for print-quality albums

---

## Data Model

### photos table (key fields)
```
id, content_hash, perceptual_hash, original_path, original_filename
source_id (FK → sources), current_path, sync_status (ok|missing|moved)
file_size, processing_level (0-2), cluster_id, is_exact_duplicate
exif_date, exif_gps_lat, exif_gps_lon, exif_camera, exif_orientation, exif_width, exif_height
location_country, location_city, location_district
quality_blur, quality_exposure, quality_aesthetic
content_category, rank_in_cluster
user_decision (keep|delete|archive|null)
created_at, updated_at
```

### photo_embeddings table
```
photo_id (PK, FK → photos(id) ON DELETE CASCADE)
embedding (BLOB — 768-dim float32 vector)
```

### clusters table
```
id, name, best_photo_id, photo_count, type (content|dup|singleton)
location_city, avg_timestamp, avg_gps_lat, avg_gps_lon
```

### sources table
```
id, path, name, added_at, last_scanned_at, photo_count, status
```

### user_corrections table
```
id, photo_id (FK), field, old_value, new_value, created_at
```

### operations table
```
id, operation_type, level, source_path, dest_path, photo_id (FK), created_at
```

### perf_log table
```
id, run_id, stage, duration_s, items, items_label, created_at
```

### events table (future)
```
id, name, start_date, end_date, gps_lat, gps_lon, cluster_count, description
```

---

## UI / Interface

### General Concepts

**Selection** — which photo/cluster is currently active (keyboard arrows, mouse click).
One item can be selected at a time (or range with Shift).
Visual: bright border/highlight.

**Marking** — user explicitly marked item for action (Space bar on selected item).
Multiple items can be marked. Visual: dimmed + checkmark overlay.
Marking is photo-centric: gallery tracks marked photo IDs, not cluster IDs.

Selection and marking state is **global** — persists across tab switches.

### Topbar
- **Source selector** and pipeline controls (Analyze / Delete dialogs)
- **Stats buttons**: Raw (L0) / L1 / OK (L2) — clickable to mark all photos at that level
- **Search**: text input with debounce (400ms) for CLIP text search
  - Category dropdown on focus (Content/Technical sections, filters by input)
  - Category shortcut: selecting a category key filters by DB field instead of CLIP search

### Tab 1: Gallery
- Adaptive photo grid. Cell size controlled by window width and zoom level.
- Zoom: scroll wheel or +/- buttons (3 levels: small/medium/large).
- Clusters displayed as single cell: best photo shown, cluster badge top-right (count).
- Badges: dup (orange), blur, o-exp, u-exp. Search results: green similarity% badge.
- Navigation: arrow keys move selection, Enter/double-click → opens Viewer.
- Space: toggle mark on selected item(s).
- Multi-select: Shift+click (range), Ctrl/Cmd+click (individual).
- Server-paginated (useInfiniteQuery, PAGE_SIZE=200), refetch 3s/30s.

### Tab 2: Viewer
- Large photo display (main area).
- Cluster strip: horizontal filmstrip showing all photos in cluster.
- Full metadata panel: EXIF, GPS location, quality scores, category, cluster info.
- Fullscreen toggle (double-click / Enter / button). Exit: Escape.
- Navigation: arrow keys move through photos (crosses cluster boundaries).

### Tab 3: All Photos
- Database table view. All photos as rows.
- Columns: thumbnail, filename, date, camera, location, cluster, level, blur, exposure,
  aesthetic, category, user_decision.
- Sortable by any column. Filterable: by level, category (22 options), cluster, decision.
- Double-click row / Enter → opens Viewer for that photo.

### Action Bar (bottom)
- Shows count of marked items + "Clear marks" button.
- Analyze and Delete actions triggered from Topbar dialogs.
- Delete: moves file to system Trash (recoverable). Updates DB.

### Processing Panel (PipelineBanner)
- Shows pipeline progress during active processing.
- 7 stages: scanning → quality → clustering → geocoding → embeddings → merging → ranking
- Progress bars with item counts. Can run in background while browsing.

### Sync
- POST /sync/check triggers background file existence verification.
- GET /sync/status returns progress. Updates `sync_status` on photos.

---

## Technical Stack

| Layer | Technology |
|---|---|
| Desktop shell | Tauri 2.x (Rust) |
| Frontend | React 19 + TypeScript + Vite + Tailwind CSS |
| Backend | Python 3.12 + FastAPI + SQLite (WAL) |
| AI/ML | open-clip-torch (ViT-L-14), scikit-learn |
| Image processing | Pillow + pillow-heif, imagehash, exifread |
| Geocoding | reverse-geocoder (offline) |
| Python management | uv |
| Python bundling | PyInstaller (sidecar for Tauri) |

### Architecture
```
Tauri window
  └── WebView → React app (Vite build)
        └── HTTP calls → FastAPI (localhost:port)
              └── SQLite DB (~/Library/Application Support/com.photogal.desktop/photogal.db)
              └── Thumbnails (~/Library/Application Support/com.photogal.desktop/.thumbnails/)

Tauri Rust shell:
  - Launches Python sidecar (FastAPI) on startup
  - Provides: native file dialogs, app menu, window management
  - Kills sidecar on app close
```

---

## Key Design Rules

1. **Non-destructive by default** — original files never moved/deleted without user action.
2. **Incremental** — each level adds info, never loses it. Can re-run any level.
3. **Offline** — all AI/ML runs locally, no cloud dependencies.
4. **Resumable** — processing can be stopped and resumed. DB tracks state.
5. **Fast browsing** — thumbnails generated at L0 scan and cached.
6. **Cross-platform** — Mac first, Windows later. Same codebase.

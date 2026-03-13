# PhotoGal — Glossary

## Core Concepts

**Photo** — a single image file. Identified by content_hash (SHA256). Has a processing_level
indicating how much analysis has been done (0-2).

**Cluster** — a group of visually similar photos grouped together. In the Gallery, a cluster
is shown as one cell (best photo visible + badge with count). Cluster types:
- `content` — visually similar photos grouped by Union-Find (pHash + time + GPS)
- `dup` — photos with identical content_hash (exact duplicates)
- `singleton` — cluster of one (not similar to anything)

**Similarity Cluster (Level 1)** — photos grouped by Union-Find algorithm into 4 groups based
on available metadata (date+GPS, GPS-only, date-only, pHash-only). Each group has different
pHash distance thresholds. Groups never cross-merge.

**Event (Level 3, future)** — a named group of clusters representing a real-world event
(trip, holiday, birthday). Events contain multiple clusters.

**Source** — a folder on disk registered as a photo library source. App imports all photos
from it. Multiple sources can be added.

**Processing Level** — integer 0-2 on each photo indicating analysis depth:
- 0 = scanned (hash + EXIF + thumbnail + dup detection)
- 1 = quality analysis + clustering + geocoding done
- 2 = AI semantic analysis done (CLIP embeddings, categories, CLIP-merge, re-ranking)

**Selection** — the currently active photo or cluster in the UI. Navigated by keyboard arrows
or mouse click. Only the active item is "selected" (highlighted). Used for navigation.

**Marking** — a user action (Space bar) that marks the selected item(s) for a bulk action.
Marked items appear dimmed with a checkmark. Photo-centric: the marked set contains photo IDs.

**Gallery** — the main tab. Shows photos/clusters as an adaptive grid. Entry point for browsing.

**Viewer** — the photo detail tab. Shows one large photo, cluster filmstrip, metadata, fullscreen.

**All Photos** — the database table tab. Shows all photos as sortable/filterable rows.

**Thumbnail** — a small cached version (400px JPEG) generated during L0 scan for fast display.

**Best Photo** — the highest-ranked photo in a cluster (rank_in_cluster=1). Shown as the
representative image in the Gallery grid cell for that cluster.

**Rank** — quality ranking within a cluster (1 = best). Based on aesthetic score.

**Aesthetic Score** — `blur_norm * 0.6 + exposure_norm * 0.4`. Level 1 heuristic
(blur + exposure normalized). Used for ranking within clusters.

**Technical Photo** — a photo classified by CLIP as non-photographic content: screenshot,
document, receipt, meme, QR code, etc. Gets a technical `content_category`.

**user_decision** — the user's explicit action decision for a photo:
- `keep` — mark to keep
- `delete` — mark for deletion (moves to Trash)
- `archive` — mark for archiving
- null — no decision yet

**pHash** — perceptual hash. A compact fingerprint of image visual content. Two photos with
small Hamming distance are considered visually similar. Thresholds vary by group (4-12).

**CLIP** — Contrastive Language-Image Pretraining. A neural network that embeds images and
text into a shared vector space. Used for semantic understanding and category classification.
Model: ViT-L-14 (laion2b_s32b_b82k).

**Union-Find** — the clustering algorithm used at Level 1. Groups photos by pHash similarity
within time/GPS windows. Four groups with different thresholds (AB, GPS, DATE, NONE).

**CLIP Merge** — Level 2 phase that merges clusters with CLIP cosine similarity ≥ 0.90
(within time and GPS gates). Runs after category classification, before re-ranking.

**Sidecar** — in Tauri: a bundled external process (the Python/FastAPI backend) managed by
the Tauri shell. Starts on app launch, killed on app exit.

**WAL** — Write-Ahead Log. SQLite mode that allows concurrent reads during writes.

## File / Folder Terms

**original_path** — the absolute path to the photo file as originally imported.

**current_path** — current path if file was moved. null if not moved.

**sync_status** — file existence status: `ok` (exists), `missing` (not found), `moved`.

**source** — a registered library root folder (e.g., ~/Pictures/GooglePhotosExport).

## Pipeline Terms

**Embedding** — a numeric vector (768-dimensional for ViT-L-14) representing a photo's
semantic content in CLIP's latent space. Stored in `photo_embeddings` table.

**Zero-shot classification** — using CLIP to classify images using text prompts, without
training a separate classifier. Used for 22-category photo classification via prompt ensembling.

**Prompt ensembling** — averaging 3-5 text prompt embeddings per category to get a more robust
category representation for classification.

**Hamming distance** — the number of bit positions that differ between two pHash values.
Lower = more similar.

**Reverse geocoding** — converting GPS coordinates to human-readable location (country, city,
district). Done offline using the `reverse-geocoder` library.

**Processing queue** — the set of photos at a given level waiting for the next level's
analysis. get_unprocessed_photos(level=N) returns photos with processing_level < N.

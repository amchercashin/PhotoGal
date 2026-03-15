"""Analysis pipeline: L1 (quality/pHash/clustering/geocoding) + L2 (CLIP/ranking).

L1: blur + exposure + pHash + 4-group Union-Find clustering + geocoding → processing_level=1
L2: CLIP embeddings + 22-category classification (prompt ensembling) + aesthetic re-ranking → processing_level=2
"""

import bisect
import logging
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np

from photogal.config import Config
from photogal.db import Database, resolve_photo_path
from photogal.profiling import stage_timer

logger = logging.getLogger(__name__)


# ─── Phase 1: per-photo quality (runs in subprocess) ──────────────────────────

def _analyze_single_photo(filepath: str, thumb_path: str | None = None) -> dict | None:
    """Open image ONCE → pHash + blur + exposure + dimensions.

    If thumb_path is provided and exists, uses the thumbnail (JPEG, ~400px)
    for blur/exposure analysis — much faster than opening original HEIC.
    pHash is also computed from thumbnail for consistency.
    Width/height are NOT taken from thumbnail — they come from EXIF in L0.
    """
    try:
        from PIL import Image
        import imagehash
        from numpy.lib.stride_tricks import sliding_window_view

        use_thumb = thumb_path and Path(thumb_path).exists()

        if use_thumb:
            img = Image.open(thumb_path).convert("RGB")
        else:
            try:
                from pillow_heif import register_heif_opener
                register_heif_opener()
            except ImportError:
                pass
            img = Image.open(filepath).convert("RGB")

        width, height = img.size
        phash = str(imagehash.phash(img))

        gray = img.convert("L")
        arr = np.array(gray, dtype=np.float64)

        kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
        windows = sliding_window_view(arr, (3, 3))
        laplacian = np.sum(windows * kernel, axis=(-2, -1))
        blur = float(np.var(laplacian))
        exposure = float(np.mean(arr))

        result = {
            "filepath": filepath,
            "perceptual_hash": phash,
            "quality_blur": blur,
            "quality_exposure": exposure,
        }
        # Only provide dimensions from original, not from thumbnail
        if not use_thumb:
            result["exif_width"] = width
            result["exif_height"] = height
        return result
    except Exception:
        return None



# ─── Geocoding ─────────────────────────────────────────────────────────────────

_geocoder = None


def _get_geocoder():
    global _geocoder
    if _geocoder is None:
        try:
            import reverse_geocoder as rg
            _geocoder = rg
        except ImportError:
            _geocoder = False
    return _geocoder if _geocoder is not False else None


def _reverse_geocode_batch(photos: list) -> dict[int, dict]:
    """Batch reverse geocode. Returns {photo_id: {location fields}}."""
    rg = _get_geocoder()
    if rg is None:
        logger.info("Geocoder not available, skipping geocoding for %d photos", len(photos))
        return {}
    gps_photos = [
        (p, p["exif_gps_lat"], p["exif_gps_lon"])
        for p in photos
        if p["exif_gps_lat"] is not None and p["exif_gps_lon"] is not None
    ]
    if not gps_photos:
        return {}
    coords = [(lat, lon) for _, lat, lon in gps_photos]
    try:
        results = rg.search(coords, mode=1, verbose=False)
    except Exception as e:
        logger.warning("Geocoding batch failed for %d photos: %s", len(gps_photos), e)
        return {}
    geo_by_id = {}
    for (p, _, _), r in zip(gps_photos, results):
        geo_by_id[p["id"]] = {
            "location_country": r.get("cc"),
            "location_city": r.get("name"),
            "location_district": r.get("admin2"),
        }
    return geo_by_id


# ─── Clustering helpers ─────────────────────────────────────────────────────────

from photogal.pipeline.helpers import haversine_m as _haversine_m
from photogal.pipeline.helpers import parse_exif_date as _parse_exif_date


def _photo_group(photo) -> str:
    """Return group name: 'AB', 'GPS', 'DATE', or 'NONE'."""
    has_date = bool(photo["exif_date"])
    has_gps = photo["exif_gps_lat"] is not None and photo["exif_gps_lon"] is not None
    if has_date and has_gps:
        return "AB"
    if not has_date and has_gps:
        return "GPS"
    if has_date and not has_gps:
        return "DATE"
    return "NONE"


# Group → (max_phash, use_time, use_gps)
_GROUP_PARAMS = {
    "AB":   (12, True,  True),   # date+GPS: strict pHash, CLIP merge pass handles semantic similarity
    "GPS":  (6,  False, True),   # только GPS: строгий pHash
    "DATE": (12, True,  False),  # только date: strict pHash, CLIP merge pass handles semantic similarity
    "NONE": (4,  False, False),  # только pHash: самый строгий
}


def _union_find_group(
    photos: list,
    max_phash: int,
    use_time: bool,
    use_gps: bool,
    max_time_s: float,
    max_dist_m: float,
) -> list[set[int]]:
    """Union-Find for one homogeneous group. Returns list of id-sets.

    For time-based groups (AB, DATE): photos sorted by date, inner loop breaks
    on time_diff > max_time_s → O(n×k) where k = window size (~20).
    For non-time groups (NONE, GPS): full O(n²) — acceptable for small groups.
    """
    n = len(photos)
    if n == 0:
        return []

    phashes_int = [
        int(p["perceptual_hash"], 16) if p["perceptual_hash"] else None
        for p in photos
    ]
    dates = [_parse_exif_date(p["exif_date"]) for p in photos]

    # Sort by date for time-based groups to enable sliding window
    if use_time:
        order = sorted(range(n), key=lambda i: (dates[i] is None, dates[i]))
        photos = [photos[i] for i in order]
        phashes_int = [phashes_int[i] for i in order]
        dates = [dates[i] for i in order]

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        if phashes_int[i] is None:
            continue
        for j in range(i + 1, n):
            if phashes_int[j] is None:
                continue
            if use_time:
                di, dj = dates[i], dates[j]
                if di is None or dj is None:
                    continue
                if abs((di - dj).total_seconds()) > max_time_s:
                    break  # sorted by date → all further j are even farther
            if use_gps:
                lat_i, lon_i = photos[i]["exif_gps_lat"], photos[i]["exif_gps_lon"]
                lat_j, lon_j = photos[j]["exif_gps_lat"], photos[j]["exif_gps_lon"]
                if lat_i is None or lat_j is None:
                    continue
                if _haversine_m(lat_i, lon_i, lat_j, lon_j) > max_dist_m:
                    continue
            if bin(phashes_int[i] ^ phashes_int[j]).count("1") > max_phash:
                continue
            union(i, j)

    groups_map: dict[int, set[int]] = {}
    for idx in range(n):
        root = find(idx)
        groups_map.setdefault(root, set()).add(photos[idx]["id"])
    return list(groups_map.values())


def _build_similarity_groups(
    photos: list,
    max_time_s: float,
    max_distance_m: float,
) -> list[set[int]]:
    """4-group Union-Find clustering. Photos from different groups never merge."""
    group_AB   = [p for p in photos if _photo_group(p) == "AB"]
    group_GPS  = [p for p in photos if _photo_group(p) == "GPS"]
    group_DATE = [p for p in photos if _photo_group(p) == "DATE"]
    group_NONE = [p for p in photos if _photo_group(p) == "NONE"]

    result = []
    for name, group_photos in [("AB", group_AB), ("GPS", group_GPS), ("DATE", group_DATE), ("NONE", group_NONE)]:
        max_phash, use_time, use_gps = _GROUP_PARAMS[name]
        result += _union_find_group(group_photos, max_phash, use_time, use_gps, max_time_s, max_distance_m)
    return result


def _simple_aesthetic_score(photo, blur_threshold: float = 500.0) -> float:
    blur = photo["quality_blur"] or 0.0
    exposure = photo["quality_exposure"] or 128.0
    blur_norm = min(blur / blur_threshold, 1.0)
    exposure_norm = 1.0 - abs(exposure - 128.0) / 128.0
    exposure_norm = max(0.0, exposure_norm)
    return blur_norm * 0.6 + exposure_norm * 0.4


# ─── CLIP categories ────────────────────────────────────────────────────────────

_CATEGORIES: dict[str, list[str]] = {
    # ── Technical (8) ──
    "screenshot": [
        "a screenshot of a smartphone screen",
        "a screenshot of a computer application or web page",
        "a screenshot of a phone settings or notification menu",
        "a screenshot of a mobile app interface",
    ],
    "receipt": [
        "a photo of a paper receipt from a store",
        "a photo of a restaurant bill or check",
        "a photo of a printed receipt on thermal paper",
    ],
    "document": [
        "a photo of a paper document or official form",
        "a photo of an ID card, passport or certificate",
        "a photo of a business card or printed letter",
        "a photo of handwritten notes or a filled-in form",
    ],
    "carsharing": [
        "a photo taken inside a shared rental car or carsharing vehicle",
        "a photo of a carsharing vehicle dashboard with app screen",
        "a screenshot of a carsharing app like Yandex Drive or BelkaCar",
        "a photo of a rental car or electric scooter with company branding",
    ],
    "meme": [
        "an internet meme image with text overlay",
        "a funny picture saved from social media",
        "a demotivational poster or internet joke image",
        "a cartoon or comic strip from the internet",
    ],
    "screen_photo": [
        "a photo of a TV screen or computer monitor",
        "a photo of a presentation projected on a screen",
        "a photo taken of another display or screen",
    ],
    "qr_code": [
        "a photo of a QR code",
        "a photo of a barcode on a product label",
        "a close-up photo of a QR code or barcode for scanning",
    ],
    "reference": [
        "a close-up photo of a serial number label on equipment",
        "a photo of an electrical connector, cable port or adapter",
        "a photo of a product label, price tag or specification plate",
        "a photo of a small mechanical or electronic part or component",
    ],
    # ── Content (14) ──
    "portrait": [
        "a portrait photo of one person looking at the camera",
        "a close-up photo of a person's face",
        "a head and shoulders photo of one person",
    ],
    "selfie": [
        "a selfie taken with a front-facing camera",
        "a self-portrait photo taken at arm's length",
        "a mirror selfie of a person",
    ],
    "group_photo": [
        "a group photo with multiple people posing together",
        "a photo of friends or family standing together",
        "a photo of several people at a gathering",
    ],
    "nature": [
        "a photo of nature, landscape or outdoor scenery",
        "a photo of mountains, forests, fields or a lake",
        "a scenic photo of a park, garden or countryside",
        "a photo of a sunset or sunrise over a landscape",
    ],
    "architecture": [
        "a photo of buildings, facades or urban architecture",
        "a photo of a city street with buildings",
        "a photo of modern or historic architecture",
    ],
    "monument": [
        "a photo of a monument, statue or memorial",
        "a photo of a famous landmark or tourist attraction",
        "a photo of a historical sculpture or monument outdoors",
    ],
    "museum": [
        "a photo inside a museum or art gallery",
        "a photo of artwork, paintings or sculpture on display",
        "a photo of a museum exhibition or exhibit hall",
    ],
    "food": [
        "a photo of food on a plate or table",
        "a photo of a meal at a restaurant or cafe",
        "a photo of drinks, coffee or cocktails",
        "a close-up photo of a dish or dessert",
    ],
    "animals": [
        "a photo of an animal or pet",
        "a photo of a cat or dog",
        "a photo of a bird or wild animal in nature",
    ],
    "transport": [
        "a photo of a car, bus, train or other vehicle",
        "a photo of a bicycle, motorcycle or scooter",
        "a photo of an airplane or boat",
    ],
    "interior": [
        "a photo of an indoor room or living space",
        "a photo of a kitchen, bedroom or living room",
        "a photo of an office, workspace or shop interior",
    ],
    "sports": [
        "a photo of a person playing sports or exercising",
        "a photo of a gym, fitness workout or athletic activity",
        "a photo of a sports event or competition",
    ],
    "event": [
        "a photo of a party, birthday or celebration",
        "a photo of a wedding, holiday gathering or festive event",
        "a photo of a concert or live performance",
    ],
    "book": [
        "a photo of a book or textbook",
        "a photo of an open book with printed text",
        "a photo of a bookshelf or stack of books",
    ],
}
_TECHNICAL_CATEGORIES = {"screenshot", "receipt", "document", "carsharing", "meme", "screen_photo", "qr_code", "reference"}

# CLIP merge pass thresholds
_CLIP_MERGE_MAX_TIME_S = 180.0   # sliding window width (seconds)
_CLIP_MERGE_MAX_DIST_M = 50.0    # GPS gate (metres)
_CLIP_MERGE_THRESHOLD = 0.90     # cosine similarity threshold

# Cached category embeddings (deterministic for a given model — computed once)
_cat_avg_embs_cache: "np.ndarray | None" = None


def _get_category_embeddings(clip) -> "np.ndarray":
    """Return (22, 768) matrix of averaged+normalized category prompt embeddings. Cached."""
    global _cat_avg_embs_cache
    if _cat_avg_embs_cache is not None:
        return _cat_avg_embs_cache
    cat_avg_embs = []
    for key in _CATEGORIES:
        prompts = _CATEGORIES[key]
        embs = clip.embed_texts(prompts)
        avg = embs.mean(axis=0)
        avg = avg / np.linalg.norm(avg)
        cat_avg_embs.append(avg)
    _cat_avg_embs_cache = np.stack(cat_avg_embs)
    return _cat_avg_embs_cache


# ─── Main Analyzer class ────────────────────────────────────────────────────────

class Analyzer:
    """Two-phase analysis: L1 (quality/clustering/geocoding) + L2 (CLIP/ranking)."""

    def __init__(self, config: Config, device: str | None = None):
        self.config = config
        self.device = device
        self._clip = None

    def _get_clip(self):
        if self._clip is None:
            from photogal.api.deps import get_clip
            self._clip = get_clip()
        return self._clip

    # ── L1 entry points ────────────────────────────────────────────────────────

    def run(self, db: Database, progress_callback=None, stage_callback=None) -> dict:
        """Full L1 analysis of all photos at level 0."""
        photos = db.get_unprocessed_photos(level=1)
        if not photos:
            return {"processed": 0, "errors": 0}
        return self._run_l1(db, photos, scoped=False,
                            progress_callback=progress_callback,
                            stage_callback=stage_callback)

    def run_for_ids(self, db: Database, photo_ids: list[int],
                    progress_callback=None, stage_callback=None) -> dict:
        """L1 + L2 analysis for specific photo IDs (incremental clustering)."""
        all_photos = db.get_photos_by_ids(photo_ids)
        l1_photos = [p for p in all_photos if p["processing_level"] < 1]
        l2_photos_initial = [p for p in all_photos if p["processing_level"] < 2]

        result = {"processed": 0, "errors": 0}

        if l1_photos:
            r1 = self._run_l1(db, l1_photos, scoped=True,
                               progress_callback=progress_callback,
                               stage_callback=stage_callback)
            result["processed"] += r1["processed"]
            result["errors"] += r1["errors"]

        # Refresh photo list after L1 to pick up newly promoted photos
        fresh = db.get_photos_by_ids(photo_ids)
        l2_photos = [p for p in fresh if p["processing_level"] < 2]
        # also include those that were already level 1 before but not yet level 2
        l2_ids_done = {p["id"] for p in l2_photos}
        for p in l2_photos_initial:
            if p["processing_level"] == 1 and p["id"] not in l2_ids_done:
                l2_photos.append(p)

        if l2_photos:
            r2 = self._run_clip(db, l2_photos,
                                 progress_callback=progress_callback,
                                 stage_callback=stage_callback)
            result["processed"] += r2["processed"]
            result["errors"] += r2["errors"]

        return result

    # ── L1 ─────────────────────────────────────────────────────────────────────

    def _run_l1(self, db: Database, photos, scoped: bool = False,
                progress_callback=None, stage_callback=None) -> dict:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        errors = 0

        # Phase 1: skip photos that already have quality data (merged into L0 scan)
        need_quality = [p for p in photos if not p["perceptual_hash"]]

        if need_quality:
            if stage_callback:
                stage_callback("quality", len(need_quality))

            filepaths = [resolve_photo_path(p) for p in need_quality]
            quality_results: dict[str, dict] = {}

            from photogal.config import get_thumbnail_cache_dir
            from photogal.thumbnails import get_thumbnail_path
            thumb_cache_dir = get_thumbnail_cache_dir()
            thumb_paths: list[str | None] = []
            for p in need_quality:
                tp = get_thumbnail_path(thumb_cache_dir, content_hash=p["content_hash"])
                thumb_paths.append(str(tp) if tp.exists() else None)

            with stage_timer("analyze/quality", items_label="photos") as t:
                with ProcessPoolExecutor(max_workers=self.config.max_workers) as executor:
                    futures = {
                        executor.submit(_analyze_single_photo, fp, tp): fp
                        for fp, tp in zip(filepaths, thumb_paths)
                    }
                    done_count = 0
                    for future in as_completed(futures):
                        result = future.result()
                        done_count += 1
                        if progress_callback:
                            progress_callback(done_count)
                        if result:
                            quality_results[result["filepath"]] = result
                        else:
                            errors += 1
                t.items = len(need_quality)
            db.log_perf(run_id, t.stage, t.duration_s, t.items, t.items_label)

            # Write quality results for photos that needed analysis
            quality_updates = []
            for photo in need_quality:
                fp = resolve_photo_path(photo)
                q = quality_results.get(fp, {})
                phash = q.get("perceptual_hash") or photo["perceptual_hash"]
                w = q.get("exif_width") if (q.get("exif_width") and not photo["exif_width"]) else photo["exif_width"]
                h = q.get("exif_height") if (q.get("exif_height") and not photo["exif_height"]) else photo["exif_height"]
                quality_updates.append((
                    q.get("quality_blur"), q.get("quality_exposure"),
                    phash, w, h, 1, photo["id"],
                ))
            db.update_photos_batch(
                ["quality_blur", "quality_exposure", "perceptual_hash",
                 "exif_width", "exif_height", "processing_level"],
                quality_updates,
            )
            db.commit()

        # Promote photos that already had quality from scanner to level 1
        have_quality = [p for p in photos if p["perceptual_hash"]]
        if have_quality:
            quality_updates = [(
                p["quality_blur"], p["quality_exposure"],
                p["perceptual_hash"], p["exif_width"], p["exif_height"],
                1, p["id"],
            ) for p in have_quality]
            db.update_photos_batch(
                ["quality_blur", "quality_exposure", "perceptual_hash",
                 "exif_width", "exif_height", "processing_level"],
                quality_updates,
            )
            db.commit()

        # Phase 2: clustering
        if stage_callback:
            stage_callback("clustering", len(photos))

        with stage_timer("analyze/clustering", items_label="photos") as t:
            photo_ids = [p["id"] for p in photos]
            fresh_photos = db.get_photos_by_ids(photo_ids)
            if scoped:
                self._cluster_incremental(db, fresh_photos)
            else:
                self._cluster_full(db, fresh_photos)
            t.items = len(photos)
        db.log_perf(run_id, t.stage, t.duration_s, t.items, t.items_label)

        # Phase 3: batch reverse geocoding
        if stage_callback:
            stage_callback("geocoding", len(photos))

        fresh_photos = db.get_photos_by_ids([p["id"] for p in photos])
        geo_results = _reverse_geocode_batch(fresh_photos)
        if geo_results:
            geo_updates = [
                (geo["location_country"], geo["location_city"], geo["location_district"], pid)
                for pid, geo in geo_results.items()
            ]
            db.update_photos_batch(
                ["location_country", "location_city", "location_district"],
                geo_updates,
            )
        db.commit()

        return {"processed": len(photos), "errors": errors}

    # ── Clustering ──────────────────────────────────────────────────────────────

    def _cluster_full(self, db: Database, photos):
        """Full re-cluster: reset all clusters, re-assign all level-1+ photos."""
        all_level1 = [p for p in db.get_all_photos() if p["processing_level"] >= 1]
        groups = _build_similarity_groups(
            all_level1,
            max_time_s=self.config.similarity_max_time_delta_s,
            max_distance_m=self.config.similarity_max_distance_m,
        )

        with db.conn.transaction():
            db.conn.execute("UPDATE photos SET cluster_id = NULL")
            db.conn.execute("DELETE FROM clusters")

            photo_by_id = {p["id"]: p for p in all_level1}
            self._assign_groups_to_clusters(db, groups, photo_by_id)

            # Singletons for level-0 photos still unclustered
            level0_unclustered = db.conn.execute(
                "SELECT * FROM photos WHERE processing_level = 0 AND cluster_id IS NULL"
            ).fetchall()
            for photo in level0_unclustered:
                cid = db.create_cluster(
                    name=photo["original_filename"].rsplit(".", 1)[0],
                    best_photo_id=photo["id"],
                    photo_count=1,
                    type="singleton",
                    avg_timestamp=photo["exif_date"],
                    avg_gps_lat=photo["exif_gps_lat"],
                    avg_gps_lon=photo["exif_gps_lon"],
                )
                db.conn.execute(
                    "UPDATE photos SET cluster_id = ?, rank_in_cluster = 1 WHERE id = ?",
                    (cid, photo["id"]),
                )

    @staticmethod
    def _find_time_candidates(
        sorted_photos: list,
        sorted_dates: list[datetime | None],
        target_date: datetime,
        max_time_s: float,
    ) -> list:
        """Binary search for photos within time window around target_date."""
        n = len(sorted_photos)
        # Find insertion point for (target_date - max_time_s)
        lo_dt = target_date.timestamp() - max_time_s
        hi_dt = target_date.timestamp() + max_time_s

        lo = bisect.bisect_left(
            sorted_dates, lo_dt,
            key=lambda d: d.timestamp() if d else float('inf')
        )
        hi = bisect.bisect_right(
            sorted_dates, hi_dt,
            key=lambda d: d.timestamp() if d else float('inf')
        )
        return sorted_photos[lo:hi]

    def _cluster_incremental(self, db: Database, new_photos):
        """Add new L1 photos into existing clusters or form new ones.

        Uses binary search for time-based groups (AB, DATE) to avoid O(n) scan.
        Never deletes or rebuilds existing clusters.
        """
        if not new_photos:
            return

        new_id_set = {p["id"] for p in new_photos}
        max_time = self.config.similarity_max_time_delta_s
        max_dist = self.config.similarity_max_distance_m

        # All existing level-1+ photos not in the new batch
        all_existing = [
            p for p in db.get_all_photos()
            if p["processing_level"] >= 1 and p["id"] not in new_id_set
        ]

        # Pre-partition existing photos by group and pre-sort time-based groups
        existing_by_group: dict[str, list] = {"AB": [], "GPS": [], "DATE": [], "NONE": []}
        for p in all_existing:
            existing_by_group[_photo_group(p)].append(p)

        # Sort time-based groups by date and pre-parse dates
        existing_dates: dict[str, list[datetime | None]] = {}
        for g in ("AB", "DATE"):
            group_photos = existing_by_group[g]
            parsed = [_parse_exif_date(p["exif_date"]) for p in group_photos]
            # Sort by date (nulls at end)
            order = sorted(range(len(group_photos)),
                           key=lambda i: (parsed[i] is None, parsed[i]))
            existing_by_group[g] = [group_photos[i] for i in order]
            existing_dates[g] = [parsed[i] for i in order]

        unmatched_new = []
        for new_p in new_photos:
            group = _photo_group(new_p)
            max_phash, use_time, use_gps = _GROUP_PARAMS[group]
            if not new_p["perceptual_hash"]:
                unmatched_new.append(new_p)
                continue

            new_ph = int(new_p["perceptual_hash"], 16)
            new_dt = _parse_exif_date(new_p["exif_date"])

            # Get candidates — binary search for time-based groups
            # If photo has no parseable date but group is time-based, fall back to all candidates
            if use_time and new_dt is not None:
                candidates = self._find_time_candidates(
                    existing_by_group[group], existing_dates[group],
                    new_dt, max_time,
                )
            else:
                if use_time and new_dt is None:
                    logger.info("Photo %d has no parseable date, using pHash-only matching", new_p["id"])
                candidates = existing_by_group[group]

            best_match = None
            best_dist = max_phash + 1
            for cand in candidates:
                if not cand["perceptual_hash"]:
                    continue
                if use_time and new_dt is not None:
                    cdt = _parse_exif_date(cand["exif_date"])
                    if cdt is None:
                        continue
                    if abs((new_dt - cdt).total_seconds()) > max_time:
                        continue
                if use_gps:
                    if (new_p["exif_gps_lat"] is None or cand["exif_gps_lat"] is None):
                        continue
                    if _haversine_m(
                        new_p["exif_gps_lat"], new_p["exif_gps_lon"],
                        cand["exif_gps_lat"], cand["exif_gps_lon"],
                    ) > max_dist:
                        continue
                dist = bin(new_ph ^ int(cand["perceptual_hash"], 16)).count("1")
                if dist <= max_phash and dist < best_dist:
                    best_dist = dist
                    best_match = cand

            if best_match and best_match["cluster_id"]:
                existing_cid = best_match["cluster_id"]
                db.conn.execute(
                    "UPDATE photos SET cluster_id=?, rank_in_cluster=99 WHERE id=?",
                    (existing_cid, new_p["id"]),
                )
                new_count = db.conn.execute(
                    "SELECT COUNT(*) FROM photos WHERE cluster_id=?", (existing_cid,)
                ).fetchone()[0]
                db.update_cluster(existing_cid, photo_count=new_count)
            else:
                unmatched_new.append(new_p)

        # Union-Find only among unmatched new photos
        if unmatched_new:
            groups = _build_similarity_groups(unmatched_new, max_time, max_dist)
            photo_by_id = {p["id"]: p for p in unmatched_new}
            self._assign_groups_to_clusters(db, groups, photo_by_id)

        db.commit()

    def _assign_groups_to_clusters(self, db: Database, groups: list, photo_by_id: dict):
        for group_ids in groups:
            if len(group_ids) == 1:
                pid = next(iter(group_ids))
                photo = photo_by_id[pid]
                # Check if already has a cluster (e.g., dup cluster from scanner)
                existing = db.conn.execute(
                    "SELECT cluster_id FROM photos WHERE id=?", (pid,)
                ).fetchone()
                if existing and existing["cluster_id"] is not None:
                    # Already in a cluster — preserve existing processing_level
                    continue
                cur_level = photo["processing_level"]
                new_level = max(cur_level, 1)
                cid = db.create_cluster(
                    name=photo["original_filename"].rsplit(".", 1)[0],
                    best_photo_id=pid,
                    photo_count=1,
                    type="singleton",
                    avg_timestamp=photo["exif_date"],
                    avg_gps_lat=photo["exif_gps_lat"],
                    avg_gps_lon=photo["exif_gps_lon"],
                )
                db.update_photo(pid, cluster_id=cid, rank_in_cluster=1, processing_level=new_level)
            else:
                group_photos = [photo_by_id[pid] for pid in sorted(group_ids)]
                lats = [p["exif_gps_lat"] for p in group_photos if p["exif_gps_lat"]]
                lons = [p["exif_gps_lon"] for p in group_photos if p["exif_gps_lon"]]
                avg_lat = float(np.mean(lats)) if lats else None
                avg_lon = float(np.mean(lons)) if lons else None
                dates_str = [p["exif_date"] for p in group_photos if p["exif_date"]]
                avg_ts = sorted(dates_str)[len(dates_str) // 2] if dates_str else None
                scored = [(p["id"], _simple_aesthetic_score(p)) for p in group_photos]
                scored.sort(key=lambda x: x[1], reverse=True)
                best_id = scored[0][0]
                best_photo = photo_by_id[best_id]
                cid = db.create_cluster(
                    name=best_photo["original_filename"].rsplit(".", 1)[0],
                    best_photo_id=best_id,
                    photo_count=len(group_ids),
                    type="content",
                    avg_timestamp=avg_ts,
                    avg_gps_lat=avg_lat,
                    avg_gps_lon=avg_lon,
                )
                for rank, (pid, _) in enumerate(scored, start=1):
                    cur_level = photo_by_id[pid]["processing_level"]
                    new_level = max(cur_level, 1)
                    db.update_photo(pid, cluster_id=cid, rank_in_cluster=rank, processing_level=new_level)

    # ── CLIP-based cluster merge ─────────────────────────────────────────────

    def _clip_merge_clusters(
        self,
        db: Database,
        photos: list,
        embeddings: dict[int, "np.ndarray | None"],
    ) -> int:
        """Merge clusters whose photos have CLIP cosine similarity >= 0.90.

        Sliding window over photos sorted by exif_date (window = 180s).
        Skips dup clusters and photos with user_cluster_override.
        Returns number of merges performed.
        """
        # Collect all photo info + embeddings (single JOIN query: photos + cluster type)
        batch_ids = set(embeddings.keys())
        all_l2 = db.get_photos_for_clip_merge(batch_ids)

        # Load embeddings for non-batch photos
        non_batch_ids = [p["id"] for p in all_l2 if p["id"] not in batch_ids]
        db_embs = db.get_embeddings_by_ids(non_batch_ids)

        # Build unified list, filtering out: user overrides, no cluster, no embedding, dup clusters
        photo_embs: list[tuple] = []
        for p in all_l2:
            pid = p["id"]
            if p["user_cluster_override"] is not None:
                continue
            if p["cluster_id"] is None:
                continue
            if p["cluster_type"] == "dup":
                continue
            if pid in batch_ids:
                emb = embeddings[pid]
            elif pid in db_embs:
                emb_bytes = db_embs[pid]
                emb = np.frombuffer(emb_bytes, dtype=np.float32)
            else:
                emb = None
            if emb is None:
                continue
            photo_embs.append((p, emb))

        if len(photo_embs) < 2:
            return 0

        # Sort by exif_date (nulls at end)
        def _date_key(item):
            dt = _parse_exif_date(item[0]["exif_date"]) if item[0]["exif_date"] else None
            return (dt is None, dt)
        photo_embs.sort(key=_date_key)

        dates = [
            _parse_exif_date(p["exif_date"]) if p["exif_date"] else None
            for p, _ in photo_embs
        ]

        # Union-Find
        n = len(photo_embs)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Sliding window: compare pairs within time/distance/similarity limits
        max_time_s = _CLIP_MERGE_MAX_TIME_S
        max_dist_m = _CLIP_MERGE_MAX_DIST_M
        clip_threshold = _CLIP_MERGE_THRESHOLD

        for i in range(n):
            di = dates[i]
            if di is None:
                continue
            pi_row, pi_emb = photo_embs[i]
            for j in range(i + 1, n):
                dj = dates[j]
                if dj is None:
                    continue
                if abs((di - dj).total_seconds()) > max_time_s:
                    break  # sorted by date

                pj_row, pj_emb = photo_embs[j]

                # Skip if already same cluster
                ci, cj = pi_row["cluster_id"], pj_row["cluster_id"]
                if find(i) == find(j):
                    continue

                # GPS gate: if both have GPS, must be within 50m
                lat_i, lon_i = pi_row["exif_gps_lat"], pi_row["exif_gps_lon"]
                lat_j, lon_j = pj_row["exif_gps_lat"], pj_row["exif_gps_lon"]
                if (lat_i is not None and lat_j is not None
                        and _haversine_m(lat_i, lon_i, lat_j, lon_j) > max_dist_m):
                    continue

                # CLIP cosine similarity (embeddings are L2-normalized)
                sim = float(np.dot(pi_emb, pj_emb))
                if sim >= clip_threshold:
                    union(i, j)

        # Collect merge groups from Union-Find
        groups: dict[int, list[int]] = {}
        for idx in range(n):
            root = find(idx)
            groups.setdefault(root, []).append(idx)

        # Perform merges
        n_merged = 0
        absorbed: set[int] = set()
        for member_indices in groups.values():
            if len(member_indices) < 2:
                continue
            # Collect unique cluster_ids in this group
            cluster_ids = list(dict.fromkeys(
                photo_embs[idx][0]["cluster_id"] for idx in member_indices
            ))
            if len(cluster_ids) < 2:
                continue
            keep_id = min(cluster_ids)
            for absorb_id in cluster_ids:
                if absorb_id != keep_id:
                    if absorb_id in absorbed:
                        continue
                    db.merge_clusters(keep_id, absorb_id)
                    absorbed.add(absorb_id)
                    n_merged += 1

        return n_merged

    # ── L2: CLIP ────────────────────────────────────────────────────────────────

    def run_clip(self, db: Database, photo_ids: list[int] | None = None,
                 progress_callback=None, stage_callback=None) -> dict:
        """Run CLIP analysis (L2) for specific IDs or all level-1 photos."""
        if photo_ids is not None:
            all_photos = db.get_photos_by_ids(photo_ids)
            photos = [p for p in all_photos if p["processing_level"] < 2]
        else:
            photos = db.get_unprocessed_photos(level=2)
        if not photos:
            return {"processed": 0, "errors": 0}
        return self._run_clip(db, photos,
                               progress_callback=progress_callback,
                               stage_callback=stage_callback)

    def _run_clip(self, db: Database, photos,
                  progress_callback=None, stage_callback=None) -> dict:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        errors = 0
        embedded_ids: list[int] = []  # track IDs with embeddings written (for cleanup on failure)

        # Check if CLIP model needs downloading (first launch)
        if self._clip is None and stage_callback:
            from photogal.models.clip import CLIPModel
            if not CLIPModel.is_model_cached(self.config.clip_model, self.config.clip_pretrained):
                stage_callback("downloading_model", 0)
            else:
                stage_callback("loading_model", 0)

        with stage_timer("analyze/model_load") as t_load:
            clip = self._get_clip()
        db.log_perf(run_id, t_load.stage, t_load.duration_s, 0, "model")

        # Download argos-translate in background while CLIP processes
        threading.Thread(target=self._ensure_argos, daemon=True).start()

        try:
            # Phase 4: CLIP embeddings
            if stage_callback:
                stage_callback("embeddings", len(photos))
            batch_size = (self.config.clip_batch_size_gpu if clip.device != "cpu"
                          else self.config.clip_batch_size_cpu)
            if batch_size is None:
                from photogal.device import get_device_info
                batch_size = get_device_info().get_optimal_batch_size("clip")

            embeddings: dict[int, np.ndarray | None] = {p["id"]: None for p in photos}

            # Resolve thumbnails for CLIP (resizes to 224px; 400px thumbnail suffices)
            from photogal.config import get_thumbnail_cache_dir
            from photogal.thumbnails import get_thumbnail_path
            thumb_cache_dir = get_thumbnail_cache_dir()

            with stage_timer("analyze/embeddings", items_label="photos") as t:
                for i in range(0, len(photos), batch_size):
                    batch = photos[i:i + batch_size]
                    fps = []
                    for p in batch:
                        tp = get_thumbnail_path(thumb_cache_dir, content_hash=p["content_hash"])
                        fps.append(str(tp) if tp.exists() else resolve_photo_path(p))
                    try:
                        batch_embs = clip.embed_batch(fps)
                    except RuntimeError:
                        batch_embs = []
                        for fp in fps:
                            try:
                                batch_embs.append(clip.embed_image(fp))
                            except Exception:
                                batch_embs.append(None)
                                errors += 1
                    valid_pairs = [(photo, emb) for photo, emb in zip(batch, batch_embs)
                                   if emb is not None]
                    if valid_pairs:
                        valid_embs = [emb for _, emb in valid_pairs]
                        scores = clip.aesthetic_scores_batch(valid_embs)
                        for (photo, emb), aesthetic in zip(valid_pairs, scores):
                            if not db.set_embedding(photo["id"], emb.tobytes()):
                                continue  # photo deleted during processing
                            db.update_photo(photo["id"], quality_aesthetic=aesthetic)
                            embeddings[photo["id"]] = emb
                            embedded_ids.append(photo["id"])
                    if progress_callback:
                        progress_callback(i + len(batch))
                t.items = len(photos)
            db.log_perf(run_id, t.stage, t.duration_s, t.items, t.items_label)
            db.commit()

            # Phase 5: zero-shot category classification (22 categories, prompt ensembling)
            cat_keys = list(_CATEGORIES.keys())
            cat_avg_embs = _get_category_embeddings(clip)

            classification_updates = []
            for photo in photos:
                img_emb = embeddings[photo["id"]]
                if img_emb is None:
                    continue
                sims = cat_avg_embs @ img_emb
                best_idx = int(np.argmax(sims))
                category = cat_keys[best_idx]
                is_tech = 1 if category in _TECHNICAL_CATEGORIES else 0
                classification_updates.append((category, is_tech, photo["id"]))
            db.update_photos_batch(
                ["content_category", "is_technical"],
                classification_updates,
            )
            db.commit()

            # Phase 5.5: CLIP-based cluster merge
            if stage_callback:
                stage_callback("merging", len(photos))
            with stage_timer("analyze/merging", items_label="merges") as t:
                n_merged = self._clip_merge_clusters(db, photos, embeddings)
                t.items = n_merged
            db.log_perf(run_id, t.stage, t.duration_s, t.items, t.items_label)
            db.commit()

            # Phase 6: re-rank clusters using aesthetic scores
            if stage_callback:
                stage_callback("ranking", len(photos))

            with stage_timer("analyze/ranking", items_label="clusters") as t:
                n_ranked = self._rank_clusters(db)
                t.items = n_ranked
            db.log_perf(run_id, t.stage, t.duration_s, t.items, t.items_label)

            # Promote to level 2 (batch)
            level_updates = [(2, p["id"]) for p in photos if embeddings.get(p["id"]) is not None]
            db.update_photos_batch(["processing_level"], level_updates)
            db.commit()

            # Invalidate search embedding cache so new embeddings are picked up
            from photogal.search import invalidate_cache
            invalidate_cache()

            return {"processed": len(photos), "errors": errors}

        except Exception as e:
            logger.error("L2 pipeline failed: %s", e, exc_info=True)
            if embedded_ids:
                logger.warning("Cleaning up %d partial embeddings", len(embedded_ids))
                for i in range(0, len(embedded_ids), 900):
                    batch = embedded_ids[i:i + 900]
                    placeholders = ",".join("?" * len(batch))
                    db.conn.execute(
                        f"DELETE FROM photo_embeddings WHERE photo_id IN ({placeholders})",
                        batch,
                    )
                db.commit()
            raise

    @staticmethod
    def _ensure_argos():
        """Background download of argos-translate ru→en model."""
        try:
            from photogal.translate import ensure_downloaded
            ensure_downloaded()
        except Exception:
            logger.warning("argos-translate background download failed", exc_info=True)

    def _rank_clusters(self, db: Database) -> int:
        from photogal.pipeline.embedder import _rank_clusters
        return _rank_clusters(db, self.config)

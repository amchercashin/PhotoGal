"""Level 0: File discovery, EXIF extraction, hashing, deduplication.

Each discovered photo is inserted into DB at processing_level=0.
"""

import hashlib
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import exifread


def _compute_sha256(filepath: str, buffer_size: int = 65536) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(buffer_size):
            h.update(chunk)
    return h.hexdigest()


def _extract_exif(filepath: str) -> dict:
    result = {
        "exif_date": None,
        "exif_gps_lat": None,
        "exif_gps_lon": None,
        "exif_camera": None,
        "exif_orientation": None,
        "exif_width": None,
        "exif_height": None,
    }
    try:
        with open(filepath, "rb") as f:
            tags = exifread.process_file(f, details=False)

        if "EXIF DateTimeOriginal" in tags:
            result["exif_date"] = str(tags["EXIF DateTimeOriginal"])
        elif "EXIF DateTimeDigitized" in tags:
            result["exif_date"] = str(tags["EXIF DateTimeDigitized"])
        elif "Image DateTime" in tags:
            result["exif_date"] = str(tags["Image DateTime"])

        if "GPS GPSLatitude" in tags and "GPS GPSLatitudeRef" in tags:
            result["exif_gps_lat"] = _gps_to_decimal(
                tags["GPS GPSLatitude"], tags["GPS GPSLatitudeRef"]
            )
        if "GPS GPSLongitude" in tags and "GPS GPSLongitudeRef" in tags:
            result["exif_gps_lon"] = _gps_to_decimal(
                tags["GPS GPSLongitude"], tags["GPS GPSLongitudeRef"]
            )

        if "Image Make" in tags and "Image Model" in tags:
            result["exif_camera"] = f"{tags['Image Make']} {tags['Image Model']}".strip()
        elif "Image Model" in tags:
            result["exif_camera"] = str(tags["Image Model"])

        if "Image Orientation" in tags:
            try:
                result["exif_orientation"] = int(str(tags["Image Orientation"]).split()[0])
            except (ValueError, IndexError):
                pass

        if "EXIF ExifImageWidth" in tags:
            try:
                result["exif_width"] = int(str(tags["EXIF ExifImageWidth"]))
            except ValueError:
                pass
        if "EXIF ExifImageLength" in tags:
            try:
                result["exif_height"] = int(str(tags["EXIF ExifImageLength"]))
            except ValueError:
                pass
    except Exception:
        pass

    return result


def _gps_to_decimal(gps_tag, ref_tag) -> float | None:
    try:
        values = gps_tag.values
        d = float(values[0].num) / float(values[0].den)
        m = float(values[1].num) / float(values[1].den)
        s = float(values[2].num) / float(values[2].den)
        decimal = d + m / 60.0 + s / 3600.0
        if str(ref_tag) in ("S", "W"):
            decimal = -decimal
        return decimal
    except Exception:
        return None


def _process_single_file(filepath: str, buffer_size: int, thumb_cache_dir: str | None = None) -> dict | None:
    try:
        content_hash = _compute_sha256(filepath, buffer_size)
        exif = _extract_exif(filepath)
        file_size = os.path.getsize(filepath)

        quality = {}
        if thumb_cache_dir:
            try:
                from photogal.thumbnails import generate_thumbnail, get_thumbnail_path
                generate_thumbnail(filepath, Path(thumb_cache_dir), content_hash=content_hash)
                tp = get_thumbnail_path(Path(thumb_cache_dir), content_hash=content_hash)
                thumb_path = str(tp) if tp.exists() else None
            except Exception:
                thumb_path = None

            # Quality analysis on freshly-written thumbnail (hot in OS cache)
            if thumb_path:
                try:
                    from photogal.pipeline.analyzer import _analyze_single_photo
                    q = _analyze_single_photo(filepath, thumb_path)
                    if q:
                        quality = q
                except Exception:
                    pass

        return {
            "original_path": filepath,
            "original_filename": os.path.basename(filepath),
            "content_hash": content_hash,
            "perceptual_hash": quality.get("perceptual_hash"),
            "quality_blur": quality.get("quality_blur"),
            "quality_exposure": quality.get("quality_exposure"),
            "file_size": file_size,
            "processing_level": 0,
            **exif,
        }
    except Exception:
        return None


def discover_files(scan_path: Path, extensions: frozenset[str]) -> list[Path]:
    files = []
    for root, _dirs, filenames in os.walk(scan_path):
        for fname in filenames:
            if Path(fname).suffix.lower() in extensions:
                files.append(Path(root) / fname)
    return sorted(files)


class Scanner:
    """Level 0 pipeline: discover files, extract EXIF, hash, insert into DB."""

    def __init__(self, config, max_workers: int | None = None):
        self.config = config
        self.max_workers = max_workers

    def run(self, db, source_id: int, scan_path: Path,
            pre_discovered_files: list | None = None,
            progress_callback=None) -> dict:
        """Scan a source folder, insert new photos into DB at level 0."""
        from datetime import datetime

        db.update_source(source_id, status="scanning", last_scanned_at=datetime.now().isoformat())
        db.commit()

        files = pre_discovered_files if pre_discovered_files is not None else discover_files(scan_path, self.config.supported_extensions)
        if not files:
            db.update_source(source_id, status="idle", photo_count=0)
            db.commit()
            return {"scanned": 0, "new": 0, "skipped": 0}

        new_photos = self._scan_files(db, source_id, files, progress_callback=progress_callback)

        count = db.conn.execute(
            "SELECT COUNT(*) FROM photos WHERE source_id = ?", (source_id,)
        ).fetchone()[0]
        db.update_source(source_id, status="idle", photo_count=count)

        # Update sync_status for photos in this source based on file existence
        scanned_paths = {str(f) for f in files}
        source_photos = db.conn.execute(
            "SELECT id, original_path, current_path FROM photos WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        sync_updates = []
        for row in source_photos:
            path = row["current_path"] or row["original_path"]
            status = "ok" if path in scanned_paths or os.path.exists(path) else "disconnected"
            sync_updates.append((status, row["id"]))
        if sync_updates:
            db.update_sync_status_bulk(sync_updates)

        db.commit()

        # Assign dup-clusters for exact duplicates (must run before singletons)
        if new_photos:
            self._assign_dup_clusters(db, new_photos)

        # Create singleton clusters only for photos without a cluster_id
        if new_photos:
            new_photo_rows = db.get_photos_by_ids(new_photos)
            for photo in new_photo_rows:
                if photo["cluster_id"] is None:
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
            db.commit()

        return {
            "scanned": len(files),
            "new": len(new_photos),
            "skipped": len(files) - len(new_photos),
        }

    def _assign_dup_clusters(self, db, new_ids: list[int]):
        """Create/join dup-clusters for exact duplicates found among new_ids."""
        if not new_ids:
            return
        placeholders = ",".join("?" * len(new_ids))
        rows = db.conn.execute(
            f"""
            SELECT content_hash, GROUP_CONCAT(id) as ids
            FROM photos
            WHERE content_hash IN (
                SELECT content_hash FROM photos WHERE id IN ({placeholders})
            )
            GROUP BY content_hash HAVING COUNT(*) > 1
            """,
            new_ids,
        ).fetchall()

        new_id_set = set(new_ids)
        for row in rows:
            all_ids = [int(x) for x in row["ids"].split(",")]
            new_dup_ids = [pid for pid in all_ids if pid in new_id_set]
            existing_ids = [pid for pid in all_ids if pid not in new_id_set]

            # Mark all duplicates (new and old)
            for pid in new_dup_ids:
                db.conn.execute("UPDATE photos SET is_exact_duplicate=1 WHERE id=?", (pid,))
            for pid in existing_ids:
                db.conn.execute("UPDATE photos SET is_exact_duplicate=1 WHERE id=?", (pid,))

            # Find existing cluster (if any)
            existing_cid = None
            if existing_ids:
                existing_photos = db.get_photos_by_ids(existing_ids)
                existing_cid = next(
                    (p["cluster_id"] for p in existing_photos if p["cluster_id"]), None
                )

            if existing_cid:
                # Add new duplicates to the existing cluster
                for pid in new_dup_ids:
                    db.conn.execute(
                        "UPDATE photos SET cluster_id=? WHERE id=?", (existing_cid, pid)
                    )
                new_count = db.conn.execute(
                    "SELECT COUNT(*) FROM photos WHERE cluster_id=?", (existing_cid,)
                ).fetchone()[0]
                db.update_cluster(existing_cid, photo_count=new_count)
            else:
                # All duplicates are new — create a dup cluster
                all_new_photos = db.get_photos_by_ids(new_dup_ids)
                if not all_new_photos:
                    continue
                cid = db.create_cluster(
                    name=all_new_photos[0]["original_filename"].rsplit(".", 1)[0] + " (dup)",
                    best_photo_id=all_new_photos[0]["id"],
                    photo_count=len(new_dup_ids),
                    type="dup",
                )
                for pid in new_dup_ids:
                    db.conn.execute(
                        "UPDATE photos SET cluster_id=? WHERE id=?", (cid, pid)
                    )
        db.commit()

    def _scan_files(self, db, source_id: int, files: list[Path], progress_callback=None) -> list[int]:
        new_ids = []
        batch = []
        done = 0

        from photogal.config import get_thumbnail_cache_dir
        thumb_dir = str(get_thumbnail_cache_dir())

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(_process_single_file, str(f), self.config.hash_buffer_size, thumb_dir): f
                for f in files
            }
            for future in as_completed(futures):
                done += 1
                if progress_callback:
                    progress_callback(done)
                result = future.result()
                if result is None:
                    continue
                if db.photo_exists(result["content_hash"], result["original_path"]):
                    continue
                result["source_id"] = source_id
                batch.append(result)
                if len(batch) >= self.config.batch_size:
                    ids = db.insert_photos_batch(batch)
                    new_ids.extend(ids)
                    batch = []

        if batch:
            ids = db.insert_photos_batch(batch)
            new_ids.extend(ids)

        return new_ids

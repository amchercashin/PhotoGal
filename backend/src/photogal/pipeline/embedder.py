"""CLIP-based re-ranking of clusters.

Used by analyzer.py to re-rank clusters after CLIP embeddings are computed.
"""

import numpy as np

from photogal.config import Config
from photogal.db import Database


def _rank_clusters(db: Database, config: Config | None = None) -> int:
    blur_threshold = config.blur_threshold if config else 500.0
    exposure_dark = config.exposure_dark_threshold if config else 50.0
    exposure_bright = config.exposure_bright_threshold if config else 220.0

    all_photos = db.get_photos_for_ranking()
    by_cluster: dict[int, list] = {}
    for p in all_photos:
        by_cluster.setdefault(int(p["cluster_id"]), []).append(p)

    rank_updates = []  # (rank, photo_id)
    ranked = 0
    for cluster_id, photos in by_cluster.items():
        scored = []
        for p in photos:
            aesthetic = p["quality_aesthetic"] or 5.0
            blur = p["quality_blur"]
            exposure = p["quality_exposure"]
            penalty = 0.0
            if blur is not None and blur < blur_threshold:
                penalty += 2.0
            if exposure is not None and (exposure < exposure_dark or exposure > exposure_bright):
                penalty += 1.0
            scored.append((aesthetic - penalty, p["id"], p["original_filename"]))
        scored.sort(reverse=True)
        for rank, (_, pid, _) in enumerate(scored, start=1):
            rank_updates.append((rank, pid))
        _, best_id, best_filename = scored[0]
        name = best_filename.rsplit(".", 1)[0] if best_filename else None
        db.update_cluster(cluster_id, best_photo_id=best_id, name=name)
        ranked += 1
    db.update_photos_batch(["rank_in_cluster"], rank_updates)
    db.commit()
    return ranked

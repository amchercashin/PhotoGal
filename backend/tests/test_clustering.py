"""Tests for 4-group clustering logic."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from photogal.pipeline.analyzer import _build_similarity_groups

MAX_TIME = 180.0
MAX_DIST = 50.0


def make_photo(id, date=None, lat=None, lon=None, phash="aaa0aaa0aaa0aaa0"):
    return {
        "id": id,
        "exif_date": date,
        "exif_gps_lat": lat,
        "exif_gps_lon": lon,
        "perceptual_hash": phash,
        "original_filename": f"photo_{id}.jpg",
        "quality_blur": 200.0,
        "quality_exposure": 128.0,
    }


def test_four_groups_no_cross():
    """Photos from different groups don't merge even with identical pHash."""
    p_ab   = make_photo(1, date="2024:01:01 12:00:00", lat=55.0, lon=37.0)
    p_gps  = make_photo(2, date=None,                  lat=55.0, lon=37.0)
    p_date = make_photo(3, date="2024:01:01 12:00:00", lat=None, lon=None)
    p_none = make_photo(4, date=None,                  lat=None, lon=None)
    groups = _build_similarity_groups([p_ab, p_gps, p_date, p_none], MAX_TIME, MAX_DIST)
    assert len(groups) == 4, f"Expected 4 groups, got {len(groups)}: {groups}"


def test_ab_group_time_gate():
    """AB: photos > 180s apart don't merge."""
    p1 = make_photo(1, date="2024:01:01 12:00:00", lat=55.0, lon=37.0)
    p2 = make_photo(2, date="2024:01:01 12:05:00", lat=55.0, lon=37.0)  # 300s
    groups = _build_similarity_groups([p1, p2], MAX_TIME, MAX_DIST)
    assert len(groups) == 2, f"Expected 2 groups (time > 180s), got {len(groups)}"


def test_ab_group_time_merge():
    """AB: photos < 180s apart but hamming~48 > 12 → no merge."""
    p1 = make_photo(1, date="2024:01:01 12:00:00", lat=55.0, lon=37.0, phash="aaa0aaa0aaa0aaa0")
    p2 = make_photo(2, date="2024:01:01 12:02:00", lat=55.0, lon=37.0, phash="ffffffffffffffff")  # hamming~48 → >12 → no merge
    groups = _build_similarity_groups([p1, p2], MAX_TIME, MAX_DIST)
    assert len(groups) == 2, f"hamming>12 should not merge even in AB, got {len(groups)}"


def test_ab_large_phash_same_time_gps():
    """AB: hamming=30 > 12, time=30s, same GPS → should NOT merge (strict pHash=12)."""
    base = 0x0000000000000000
    diff = (1 << 30) - 1  # bits 0-29 flipped → hamming=30
    h1 = format(base, '016x')
    h2 = format(base ^ diff, '016x')
    p1 = make_photo(1, date="2024:01:01 12:00:00", lat=55.0, lon=37.0, phash=h1)
    p2 = make_photo(2, date="2024:01:01 12:00:30", lat=55.0, lon=37.0, phash=h2)  # 30s
    groups = _build_similarity_groups([p1, p2], MAX_TIME, MAX_DIST)
    assert len(groups) == 2, f"AB hamming=30 > 12 should NOT merge, got {len(groups)}"


def test_ab_small_phash_same_time_gps():
    """AB: hamming=10 <= 12, time=30s, same GPS → should merge."""
    base = 0x0000000000000000
    diff = 0x3FF  # bits 0-9 flipped → hamming=10
    h1 = format(base, '016x')
    h2 = format(base ^ diff, '016x')
    p1 = make_photo(1, date="2024:01:01 12:00:00", lat=55.0, lon=37.0, phash=h1)
    p2 = make_photo(2, date="2024:01:01 12:00:30", lat=55.0, lon=37.0, phash=h2)
    groups = _build_similarity_groups([p1, p2], MAX_TIME, MAX_DIST)
    assert len(groups) == 1, f"AB hamming=10 <= 12 should merge, got {len(groups)}"


def test_none_group_strict_phash():
    """NONE group: pHash hamming > 4 stays separate, <= 4 merges."""
    # p1 and p2 differ by 1 bit (hamming=1, <= 4 → should merge)
    p1 = make_photo(1, date=None, lat=None, phash="aaa0aaa0aaa0aaa0")
    p2 = make_photo(2, date=None, lat=None, phash="aaa1aaa0aaa0aaa0")  # hamming ~1
    # p3 differs significantly from p1
    p3 = make_photo(3, date=None, lat=None, phash="ffffffffffffffff")  # hamming >> 4
    groups = _build_similarity_groups([p1, p2, p3], MAX_TIME, MAX_DIST)
    assert len(groups) == 2, f"Expected 2 groups (p1+p2 merged, p3 alone), got {len(groups)}"


def test_date_group_no_gps_needed():
    """DATE group (has date, no GPS): clusters only by time+pHash, not GPS."""
    p1 = make_photo(1, date="2024:01:01 12:00:00", lat=None, lon=None)
    p2 = make_photo(2, date="2024:01:01 12:01:00", lat=None, lon=None)  # 60s
    groups = _build_similarity_groups([p1, p2], MAX_TIME, MAX_DIST)
    assert len(groups) == 1, f"DATE group should merge by time alone, got {len(groups)}"


def test_gps_group_no_date_needed():
    """GPS group (has GPS, no date): clusters only by GPS+pHash, not time."""
    p1 = make_photo(1, date=None, lat=55.0, lon=37.0)
    p2 = make_photo(2, date=None, lat=55.0, lon=37.0)  # same coords
    groups = _build_similarity_groups([p1, p2], MAX_TIME, MAX_DIST)
    assert len(groups) == 1, f"GPS group should merge by location alone, got {len(groups)}"

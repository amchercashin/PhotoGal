"""Tests for CLIP category definitions."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from photogal.pipeline.analyzer import _CATEGORIES, _TECHNICAL_CATEGORIES


def test_22_categories_defined():
    """Exactly 22 categories in _CATEGORIES."""
    assert len(_CATEGORIES) == 22, f"Expected 22 categories, got {len(_CATEGORIES)}: {list(_CATEGORIES.keys())}"


def test_technical_categories_subset():
    """8 technical categories are a subset of _CATEGORIES."""
    assert _TECHNICAL_CATEGORIES <= set(_CATEGORIES.keys()), \
        f"Technical categories {_TECHNICAL_CATEGORIES} not all in {set(_CATEGORIES.keys())}"


def test_technical_category_count():
    """Exactly 8 technical categories."""
    assert len(_TECHNICAL_CATEGORIES) == 8, f"Expected 8 technical categories, got {len(_TECHNICAL_CATEGORIES)}"


def test_all_categories_have_prompts():
    """Every category has a non-empty list of prompt strings."""
    for key, prompts in _CATEGORIES.items():
        assert isinstance(prompts, list) and len(prompts) > 0, f"Category '{key}' has empty/invalid prompts"
        for p in prompts:
            assert isinstance(p, str) and len(p) > 0, f"Category '{key}' has empty prompt in list"


def test_expected_technical_categories():
    """Specific technical category keys exist."""
    for expected in ("receipt", "screenshot", "document", "carsharing", "meme", "screen_photo", "qr_code", "reference"):
        assert expected in _TECHNICAL_CATEGORIES, f"'{expected}' missing from _TECHNICAL_CATEGORIES"

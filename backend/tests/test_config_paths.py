"""Tests for platform-aware data directory paths."""

import sys
from unittest.mock import patch
from pathlib import Path

from photogal.config import get_db_path, get_thumbnail_cache_dir


def test_macos_db_path_uses_application_support(tmp_path):
    with patch("photogal.config.Path.home", return_value=tmp_path), \
         patch.object(sys, "platform", "darwin"):
        import importlib
        import photogal.config
        importlib.reload(photogal.config)
        from photogal.config import get_db_path as refreshed

        result = refreshed()
        assert "Library/Application Support/com.photogal.desktop" in str(result)
        assert result.name == "photogal.db"
        assert result.parent.exists()


def test_linux_db_path_uses_dotphotogal(tmp_path):
    with patch("photogal.config.Path.home", return_value=tmp_path), \
         patch.object(sys, "platform", "linux"):
        import importlib
        import photogal.config
        importlib.reload(photogal.config)
        from photogal.config import get_db_path as refreshed

        result = refreshed()
        assert ".photogal" in str(result)
        assert result.name == "photogal.db"


def test_macos_thumbnail_dir_uses_application_support(tmp_path):
    with patch("photogal.config.Path.home", return_value=tmp_path), \
         patch.object(sys, "platform", "darwin"):
        import importlib
        import photogal.config
        importlib.reload(photogal.config)
        from photogal.config import get_thumbnail_cache_dir as refreshed

        result = refreshed()
        assert "Library/Application Support/com.photogal.desktop" in str(result)
        assert result.name == ".thumbnails"
        assert result.exists()

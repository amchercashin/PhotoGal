"""Tests for cross-platform trash_files."""

from unittest.mock import patch, call
from photogal.trash import trash_files


@patch("photogal.trash.send2trash")
def test_trash_files_calls_send2trash(mock_s2t, tmp_path):
    """trash_files sends each file to system trash."""
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "b.jpg"
    f1.touch()
    f2.touch()
    count, errors = trash_files([str(f1), str(f2)])
    assert count == 2
    assert errors == []
    assert mock_s2t.call_count == 2


@patch("photogal.trash.send2trash")
def test_trash_files_skips_missing(mock_s2t, tmp_path):
    """Non-existent files are silently skipped."""
    count, errors = trash_files([str(tmp_path / "gone.jpg")])
    assert count == 0
    assert errors == []
    mock_s2t.assert_not_called()


@patch("photogal.trash.send2trash", side_effect=OSError("permission denied"))
def test_trash_files_collects_errors(mock_s2t, tmp_path):
    """Errors are collected, not raised."""
    f = tmp_path / "locked.jpg"
    f.touch()
    count, errors = trash_files([str(f)])
    assert count == 0
    assert len(errors) == 1
    assert "permission denied" in errors[0]

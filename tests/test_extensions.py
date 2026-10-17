"""Input validation for the extension tools (no database or network needed)."""

import pytest

from src import extensions


def test_remote_part_bounds():
    with pytest.raises(ValueError, match="part must be"):
        extensions._url(50)
    assert extensions._url(7).endswith("part_007.parquet")


def test_workspace_name_is_derived_not_supplied():
    from datetime import datetime
    name = extensions._ws_name("bgl_logs", datetime(2005, 6, 3), datetime(2005, 6, 17, 12, 30))
    assert name == "ws_bgl_logs_20050603000000_20050617123000"

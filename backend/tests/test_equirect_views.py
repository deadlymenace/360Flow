"""Perspective split/reproject round trip, and the structure check."""

from __future__ import annotations

import numpy as np

from services.equirect_views import reproject_views, split_views, structure_score
from tests.test_stitch import _make_equirect


def _band_mae(original: np.ndarray, rebuilt: np.ndarray, limit_deg: float) -> float:
    height = original.shape[0]
    rows = np.arange(height)
    lat = 90.0 - (rows + 0.5) / height * 180.0
    band = np.abs(lat) < limit_deg
    return float(np.mean(np.abs(original[band].astype(np.float32) - rebuilt[band].astype(np.float32))))


def test_split_reproject_round_trip_keeps_poles_and_equator():
    original = _make_equirect(width=768, height=384, seed=4)
    views = split_views(original, width=320, height=240)
    assert len(views) == 5
    rebuilt = reproject_views(original, views)
    assert rebuilt.shape == original.shape
    assert rebuilt.shape[1] == rebuilt.shape[0] * 2

    height = original.shape[0]
    rows = np.arange(height)
    lat = 90.0 - (rows + 0.5) / height * 180.0
    poles = np.abs(lat) > 55
    assert np.array_equal(rebuilt[poles], original[poles])
    assert _band_mae(original, rebuilt, 18) < 16.0

    equator = np.abs(lat) < 18
    seam = np.abs(rebuilt[equator, 0].astype(np.float32) - rebuilt[equator, -1].astype(np.float32))
    assert float(seam.mean()) < 12.0


def test_structure_score_flags_moved_edges_and_accepts_a_grade():
    original = _make_equirect(width=320, height=240, seed=2)
    assert structure_score(original, original) > 0.9
    graded = original.copy()
    graded[:, :, 2] = np.clip(graded[:, :, 2].astype(np.int16) + 18, 0, 255).astype(np.uint8)
    assert structure_score(original, graded) > 0.45
    walls = np.full_like(original, 30)
    walls[:, 30:70] = (220, 220, 220)
    walls[:, 180:210] = (40, 40, 200)
    moved = np.full_like(walls, 30)
    moved[:, 90:130] = (220, 220, 220)
    moved[:, 240:270] = (40, 40, 200)
    assert structure_score(walls, moved) < 0.45

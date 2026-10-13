"""Synthetic 360° capture: overlapping pinhole views must stitch to a 2:1 equirect."""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from services.stitcher import _fill_poles, render_perspective, stitch_images

HFOV = 70.0
SEGMENT_COUNT = 16


def _make_equirect(width: int = 2048, height: int = 1024, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    ys, xs = np.mgrid[0:height, 0:width]
    lon = (xs + 0.5) / width * 2 * np.pi - np.pi
    lat = np.pi / 2 - (ys + 0.5) / height * np.pi
    image = np.zeros((height, width, 3), dtype=np.float32)
    image[..., 0] = 50 + 35 * np.sin(lat * 3) + 25 * np.cos(lon * 5)
    image[..., 1] = 90 + 40 * np.cos(lat * 2) + 20 * np.sin(lon * 7)
    image[..., 2] = 130 + 30 * np.sin(lon * 3) + 18 * np.cos(lat * 4)
    lon_deg = np.degrees(lon)
    lat_deg = np.degrees(lat)
    grid = (np.abs((lon_deg + 180) % 10) < 0.25) | (np.abs((lat_deg + 90) % 10) < 0.35)
    image[grid] = (240, 240, 240)
    image += rng.normal(0, 10, image.shape)
    return np.clip(image, 0, 255).astype(np.uint8)


def _paint_marker(image: np.ndarray, lon_deg: float, lat_deg: float, color: tuple[int, int, int]) -> None:
    height, width = image.shape[:2]
    x = int(round((lon_deg + 180.0) / 360.0 * width)) % width
    y = int(round((90.0 - lat_deg) / 180.0 * height))
    y = min(max(y, 0), height - 1)
    cv2.circle(image, (x, y), 16, color, -1)


def _write_views(equirect: np.ndarray, directory: Path, view_w: int = 360, view_h: int = 480) -> list[float]:
    directory.mkdir(parents=True, exist_ok=True)
    yaws = [index * (360.0 / SEGMENT_COUNT) for index in range(SEGMENT_COUNT)]
    for index, yaw in enumerate(yaws):
        view = render_perspective(equirect, yaw, HFOV, view_w, view_h)
        path = directory / f"{index:02d}.png"
        assert cv2.imwrite(str(path), view)
    return yaws


def _marker_centroid(image: np.ndarray, color: tuple[int, int, int], tolerance: float = 50.0) -> tuple[float, float]:
    distance = np.linalg.norm(image.astype(np.float32) - np.array(color, dtype=np.float32), axis=2)
    ys, xs = np.where(distance < tolerance)
    assert len(xs) > 20, f"marker {color} not found ({len(xs)} px)"
    return float(xs.mean()), float(ys.mean())


def test_perspective_center_looks_along_yaw():
    """The view renderer is the inverse of the stitch camera, checked absolutely."""
    equirect = np.zeros((512, 1024, 3), dtype=np.uint8)
    cv2.circle(equirect, (512, 256), 10, (0, 0, 255), -1)  # lon 0, lat 0, red
    cv2.circle(equirect, (768, 256), 10, (0, 255, 0), -1)  # lon +90, green

    forward = render_perspective(equirect, yaw_deg=0, hfov_deg=70, out_w=180, out_h=180)
    center = forward[90, 90]
    assert center[2] > 200 and center[1] < 40 and center[0] < 40

    right = render_perspective(equirect, yaw_deg=90, hfov_deg=70, out_w=180, out_h=180)
    center = right[90, 90]
    assert center[1] > 200 and center[2] < 40 and center[0] < 40


def test_two_colors_land_on_their_yaw(tmp_path: Path):
    red = np.full((200, 160, 3), (0, 0, 220), dtype=np.uint8)
    green = np.full((200, 160, 3), (0, 210, 0), dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "00.png"), red)
    cv2.imwrite(str(tmp_path / "01.png"), green)
    output = tmp_path / "pano.jpg"
    assert stitch_images(
        [tmp_path / "00.png", tmp_path / "01.png"],
        output,
        yaws_deg=[0.0, 90.0],
        hfov_deg=60.0,
        refine=False,
    )
    panorama = cv2.imread(str(output))
    height, width = panorama.shape[:2]
    assert width == height * 2
    red_px = panorama[height // 2, width // 2]
    green_px = panorama[height // 2, int(round(0.75 * (width - 1)))]
    assert red_px[2] > 180 and red_px[1] < 40
    assert green_px[1] > 160 and green_px[2] < 50


def test_synthetic_loop_is_two_to_one(tmp_path: Path):
    equirect = _make_equirect()
    _paint_marker(equirect, 0, 0, (0, 0, 255))
    _paint_marker(equirect, 90, 4, (0, 255, 0))
    _paint_marker(equirect, 170, -4, (255, 80, 0))
    _paint_marker(equirect, -90, 0, (0, 255, 255))

    view_dir = tmp_path / "views"
    yaws = _write_views(equirect, view_dir)
    output = tmp_path / "pano.jpg"
    assert stitch_images(sorted(view_dir.glob("*.png")), output, yaws_deg=yaws, hfov_deg=HFOV, refine=False)

    panorama = cv2.imread(str(output))
    assert panorama is not None
    height, width = panorama.shape[:2]
    assert width == height * 2
    assert width >= 640

    band0 = int(round((90 - 22) / 180 * height))
    band1 = int(round((90 + 22) / 180 * height))
    resized = cv2.resize(equirect, (width, height), interpolation=cv2.INTER_AREA)
    stitched_band = panorama[band0:band1].astype(np.float32)
    truth_band = resized[band0:band1].astype(np.float32)
    best_shift = 0
    best_mae = 1e9
    for shift in range(-3, 4):
        mae = float(np.mean(np.abs(np.roll(stitched_band, shift, axis=1) - truth_band)))
        if mae < best_mae:
            best_mae = mae
            best_shift = shift
    assert abs(best_shift) <= 2, f"yaw drift of {best_shift}px (mae {best_mae:.2f})"
    assert best_mae < 18.0, f"equirect reconstruction MAE {best_mae:.2f}"

    expectations = [
        ((0, 0, 255), 0.0, 0.0),
        ((0, 255, 0), 90.0, 4.0),
        ((255, 80, 0), 170.0, -4.0),
        ((0, 255, 255), -90.0, 0.0),
    ]
    for color, lon, lat in expectations:
        x, y = _marker_centroid(panorama, color)
        expected_x = ((lon + 180.0) / 360.0) * width
        expected_y = ((90.0 - lat) / 180.0) * height
        # Markers on a loop: compare the shortest horizontal distance.
        dx = min(abs(x - expected_x), width - abs(x - expected_x))
        assert dx < width * 0.02, f"{color} longitude off by {dx:.1f}px"
        assert abs(y - expected_y) < height * 0.03, f"{color} latitude off by {y - expected_y:.1f}px"

    # A 16-shot ring does not photograph the poles. The caps must still be a
    # smooth fill, and the longitude cut has to agree.
    assert float(panorama[:6].mean()) > 20, "zenith cap is black"
    assert float(panorama[-6:].mean()) > 20, "nadir cap is black"
    seam = np.abs(panorama[:, 0].astype(np.float32) - panorama[:, -1].astype(np.float32))
    assert float(seam.mean()) < 4.0, f"left/right edges differ by {seam.mean():.2f}"

    artifact_dir = os.environ.get("STITCH_ARTIFACT_DIR")
    if artifact_dir:
        destination = Path(artifact_dir)
        destination.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(destination / "synthetic-equirect.jpg"), panorama, [cv2.IMWRITE_JPEG_QUALITY, 95])


def test_nadir_fill_leaves_the_photographed_floor_sharp():
    """The bottom cap fills unseen rows and does not smear the real floor."""
    height, width = 180, 360
    image = np.zeros((height, width, 3), dtype=np.float32)
    valid = np.zeros((height, width), dtype=bool)
    checker = ((np.arange(width) // 4) % 2) * 200 + 20
    image[30:121, :, 1] = checker
    image[30:121, :, 0] = 40
    image[30:121, :, 2] = 90
    valid[30:121] = True
    before = image[100].copy()

    filled = _fill_poles(image, valid)

    assert np.allclose(filled[100], before), "photographed floor was blended into the cap"
    assert float(np.std(filled[100, :, 1])) > 80
    assert float(filled[-4:].mean()) > 20, "nadir cap is black"
    assert float(np.std(filled[-1, :, 1])) < 5, "nadir pole is a stretched smear"


def test_refinement_still_closes_the_loop(tmp_path: Path):
    equirect = _make_equirect(width=1024, height=512, seed=9)
    _paint_marker(equirect, 0, 0, (0, 0, 255))
    view_dir = tmp_path / "views"
    yaws = _write_views(equirect, view_dir, view_w=280, view_h=360)
    output = tmp_path / "pano.jpg"
    assert stitch_images(sorted(view_dir.glob("*.png")), output, yaws_deg=yaws, hfov_deg=HFOV, refine=True)
    panorama = cv2.imread(str(output))
    height, width = panorama.shape[:2]
    assert width == height * 2
    x, y = _marker_centroid(panorama, (0, 0, 255))
    assert abs(x - width / 2) < width * 0.03
    assert abs(y - height / 2) < height * 0.04

"""Split an equirect into overlapping views and blend edits back.

General image editors damage a 2:1 panorama at the longitude seam and at the
poles. Staging therefore edits a ring of perspective views, leaves the
original ceiling and floor in place, and reprojects with a wraparound blend.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from services.stitcher import render_perspective

VIEW_YAWS = (0.0, 72.0, 144.0, 216.0, 288.0)
VIEW_HFOV = 96.0
VIEW_WIDTH = 480
VIEW_HEIGHT = 360
# Original pixels stay put above this latitude. The fade starts 14° below it.
POLE_LIMIT_DEG = 50.0


@dataclass
class PerspectiveView:
    yaw_deg: float
    hfov_deg: float
    image: np.ndarray


def split_views(
    equirect_bgr: np.ndarray,
    yaws: tuple[float, ...] = VIEW_YAWS,
    hfov_deg: float = VIEW_HFOV,
    width: int = VIEW_WIDTH,
    height: int = VIEW_HEIGHT,
) -> list[PerspectiveView]:
    return [
        PerspectiveView(
            yaw_deg=float(yaw),
            hfov_deg=hfov_deg,
            image=render_perspective(equirect_bgr, yaw, hfov_deg, width, height),
        )
        for yaw in yaws
    ]


def reproject_views(
    original_bgr: np.ndarray,
    views: list[PerspectiveView],
    pole_limit_deg: float = POLE_LIMIT_DEG,
) -> np.ndarray:
    """Blend perspective edits onto the equator. Poles stay the original photo."""
    height, width = original_bgr.shape[:2]
    acc = np.zeros((height, width, 3), dtype=np.float32)
    weight_sum = np.zeros((height, width), dtype=np.float32)
    cols = np.arange(width)
    rows = np.arange(height)
    lon = (cols + 0.5) / width * 2 * np.pi - np.pi
    lat = np.pi / 2 - (rows + 0.5) / height * np.pi
    lon_grid, lat_grid = np.meshgrid(lon, lat)
    lat_deg = np.degrees(lat_grid)
    fade = np.clip((pole_limit_deg - np.abs(lat_deg)) / 14.0, 0.0, 1.0)
    lat_weight = (fade * fade * (3.0 - 2.0 * fade)).astype(np.float32)

    cos_lat = np.cos(lat_grid)
    world_x = np.sin(lon_grid) * cos_lat
    world_y = np.sin(lat_grid)
    world_z = np.cos(lon_grid) * cos_lat

    for view in views:
        patch, weight = _sample_view(view, world_x, world_y, world_z)
        weight = weight * lat_weight
        acc += patch * weight[..., None]
        weight_sum += weight

    base = original_bgr.astype(np.float32)
    alpha = np.clip(weight_sum, 0.0, 1.0)[..., None]
    covered = weight_sum > 1e-4
    blended = base.copy()
    blended[covered] = acc[covered] / weight_sum[covered, None]
    out = blended * alpha + base * (1.0 - alpha)
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def structure_score(original_bgr: np.ndarray, edited_bgr: np.ndarray) -> float:
    """Recall of the original edges after a small dilation.

    Added furniture creates new edges, so this does not penalize extra lines.
    It drops when walls or windows move and the original edges disappear.
    """
    edited = edited_bgr
    if edited.shape[:2] != original_bgr.shape[:2]:
        edited = cv2.resize(edited, (original_bgr.shape[1], original_bgr.shape[0]), interpolation=cv2.INTER_AREA)
    original_edges = _edges(original_bgr)
    edited_edges = _edges(edited)
    base = int(original_edges.sum())
    if base < 20:
        return 1.0
    return float(np.logical_and(original_edges, edited_edges).sum()) / float(base)


def _edges(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    return cv2.dilate(edges, np.ones((5, 5), np.uint8)) > 0


def _sample_view(
    view: PerspectiveView,
    world_x: np.ndarray,
    world_y: np.ndarray,
    world_z: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    src_h, src_w = view.image.shape[:2]
    hfov = np.radians(view.hfov_deg)
    focal = (src_w / 2.0) / np.tan(hfov / 2.0)
    yaw = np.radians(view.yaw_deg)
    cos_y, sin_y = float(np.cos(yaw)), float(np.sin(yaw))
    x1 = world_x * cos_y - world_z * sin_y
    y1 = world_y
    z1 = world_x * sin_y + world_z * cos_y
    safe = z1 > 0.05
    z_safe = np.where(safe, z1, 1.0)
    cx = (src_w - 1) / 2.0
    cy = (src_h - 1) / 2.0
    map_x = (cx + focal * (x1 / z_safe)).astype(np.float32)
    map_y = (cy - focal * (y1 / z_safe)).astype(np.float32)
    patch = cv2.remap(
        view.image,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    ).astype(np.float32)
    inside = safe & (map_x >= 1) & (map_y >= 1) & (map_x <= src_w - 2) & (map_y <= src_h - 2)
    edge_x = max(src_w * 0.08, 2.0)
    edge_y = max(src_h * 0.08, 2.0)
    nx = np.minimum(map_x / edge_x, (src_w - 1 - map_x) / edge_x)
    ny = np.minimum(map_y / edge_y, (src_h - 1 - map_y) / edge_y)
    t = np.clip(np.minimum(nx, ny), 0.0, 1.0)
    feather = (t * t * (3.0 - 2.0 * t)).astype(np.float32)
    return patch, feather * inside.astype(np.float32)

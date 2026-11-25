"""Equirectangular 360-degree panorama to overlapping pinhole perspective crops.

World Labs Atlas accepts only perspective pinhole images (no equirectangular
input). This module slices a 2:1 equirectangular panorama into 8-12 overlapping
pinhole crops across 2 or 3 pitch rows and computes the exact PinholeCamera
intrinsics and rotation-only RUB extrinsics for each slice.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

SUPPORTED_ATLAS_GRIDS: tuple[tuple[int, int], ...] = (
    (1280, 720),
    (720, 1280),
    (960, 720),
    (528, 704),
)


@dataclass(frozen=True)
class PanoSlice:
    index: int
    name: str
    yaw_deg: float
    pitch_deg: float
    hfov_deg: float
    width: int
    height: int
    image_bytes: bytes
    mime_type: str
    camera: dict


def _yaw_pitch_to_rub_matrix_and_quat(yaw_deg: float, pitch_deg: float) -> tuple[np.ndarray, list[float]]:
    """Return (3x3 R_world_from_cam, [qx, qy, qz, qw]) in Atlas RUB coordinates.

    Atlas RUB convention:
    - +X right, +Y up, +Z back (camera looks along -Z).
    - Positive yaw turns right -> negative rotation about world +Y.
    - Positive pitch looks up -> positive rotation about local +X.
    """
    yaw_rad = math.radians(yaw_deg)
    pitch_rad = math.radians(pitch_deg)

    # q_y(-yaw) * q_x(+pitch)
    hy = -0.5 * yaw_rad
    hp = 0.5 * pitch_rad
    cy, sy = math.cos(hy), math.sin(hy)
    cp, sp = math.cos(hp), math.sin(hp)

    # Hamilton product (0, sy, 0, cy) * (sp, 0, 0, cp) in XYZW
    qx = cy * sp
    qy = sy * cp
    qz = -sy * sp
    qw = cy * cp
    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw) or 1.0
    quat = [round(qx / norm, 8), round(qy / norm, 8), round(qz / norm, 8), round(qw / norm, 8)]

    # Rotation matrices: R_y(-yaw) @ R_x(+pitch)
    cos_y, sin_y = math.cos(-yaw_rad), math.sin(-yaw_rad)
    cos_p, sin_p = math.cos(pitch_rad), math.sin(pitch_rad)
    ry = np.array(
        [
            [cos_y, 0.0, sin_y],
            [0.0, 1.0, 0.0],
            [-sin_y, 0.0, cos_y],
        ],
        dtype=np.float64,
    )
    rx = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, cos_p, -sin_p],
            [0.0, sin_p, cos_p],
        ],
        dtype=np.float64,
    )
    return ry @ rx, quat


def default_slice_plan(count: int = 10) -> list[tuple[float, float]]:
    """Return (yaw_deg, pitch_deg) pairs across 2 or 3 pitch rows (8-12 views)."""
    if count == 8:
        # 2 rows: 4 horizon/low (-8 deg) + 4 upper (+22 deg), staggered 45 deg
        return [(i * 90.0, -8.0) for i in range(4)] + [(i * 90.0 + 45.0, 22.0) for i in range(4)]
    if count == 10:
        # 2 rows: 6 main-level (-12 deg) + 4 upper (+22 deg), staggered
        return [(i * 60.0, -12.0) for i in range(6)] + [(i * 90.0 + 45.0, 22.0) for i in range(4)]
    if count == 12:
        # 3 rows: 4 lower (-22 deg) + 5 horizon (0 deg) + 3 upper (+25 deg)
        return (
            [(i * 90.0 + 45.0, -22.0) for i in range(4)]
            + [(i * 72.0, 0.0) for i in range(5)]
            + [(i * 120.0 + 30.0, 25.0) for i in range(3)]
        )
    raise ValueError("count must be 8, 10, or 12 for default slice plans")


def pinhole_camera_dict(
    yaw_deg: float,
    pitch_deg: float,
    *,
    width: int = 1280,
    height: int = 720,
    hfov_deg: float = 90.0,
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[np.ndarray, dict]:
    if (width, height) not in SUPPORTED_ATLAS_GRIDS:
        raise ValueError(f"Unsupported Atlas grid {(width, height)}; expected one of {SUPPORTED_ATLAS_GRIDS}")
    if not (45.0 <= hfov_deg <= 120.0):
        raise ValueError("hfov_deg must be between 45 and 120 degrees")
    fx = width / (2.0 * math.tan(math.radians(hfov_deg) / 2.0))
    fy = fx
    cx = width / 2.0
    cy = height / 2.0
    rot, quat = _yaw_pitch_to_rub_matrix_and_quat(yaw_deg, pitch_deg)
    camera = {
        "extrinsics": {
            "position": [float(position[0]), float(position[1]), float(position[2])],
            "quaternion": quat,
            "coordinateSystem": "rub",
        },
        "intrinsics": {
            "fx": round(fx, 4),
            "fy": round(fy, 4),
            "cx": round(cx, 4),
            "cy": round(cy, 4),
            "width": int(width),
            "height": int(height),
        },
    }
    return rot, camera


def _sample_equirect(eq_arr: np.ndarray, rot: np.ndarray, camera: dict) -> np.ndarray:
    intr = camera["intrinsics"]
    w = int(intr["width"])
    h = int(intr["height"])
    fx = float(intr["fx"])
    fy = float(intr["fy"])
    cx = float(intr["cx"])
    cy = float(intr["cy"])

    u = np.arange(w, dtype=np.float64)[None, :] + 0.5
    v = np.arange(h, dtype=np.float64)[:, None] + 0.5
    x_cam = (u - cx) / fx
    y_cam = -(v - cy) / fy
    z_cam = np.full((h, w), -1.0, dtype=np.float64)

    # Broadcast x_cam/y_cam/z_cam into world rays via rot (3x3)
    x_cam_b = np.broadcast_to(x_cam, (h, w))
    y_cam_b = np.broadcast_to(y_cam, (h, w))
    dx = rot[0, 0] * x_cam_b + rot[0, 1] * y_cam_b + rot[0, 2] * z_cam
    dy = rot[1, 0] * x_cam_b + rot[1, 1] * y_cam_b + rot[1, 2] * z_cam
    dz = rot[2, 0] * x_cam_b + rot[2, 1] * y_cam_b + rot[2, 2] * z_cam

    norm = np.sqrt(dx * dx + dy * dy + dz * dz)
    dy_n = np.clip(dy / norm, -1.0, 1.0)
    lon = np.arctan2(dx, -dz)  # [-pi, pi], 0 at -Z center
    lat = np.arcsin(dy_n)      # [-pi/2, pi/2], +pi/2 at +Y top

    eq_h, eq_w = eq_arr.shape[:2]
    src_x = (lon / (2.0 * math.pi) + 0.5) * eq_w - 0.5
    src_y = (0.5 - lat / math.pi) * eq_h - 0.5

    x0 = np.floor(src_x).astype(np.int32)
    y0 = np.floor(src_y).astype(np.int32)
    wx = (src_x - x0)[..., None]
    wy = (src_y - y0)[..., None]

    x0_w = np.mod(x0, eq_w)
    x1_w = np.mod(x0 + 1, eq_w)
    y0_c = np.clip(y0, 0, eq_h - 1)
    y1_c = np.clip(y0 + 1, 0, eq_h - 1)

    c00 = eq_arr[y0_c, x0_w].astype(np.float32)
    c10 = eq_arr[y0_c, x1_w].astype(np.float32)
    c01 = eq_arr[y1_c, x0_w].astype(np.float32)
    c11 = eq_arr[y1_c, x1_w].astype(np.float32)

    out = (
        c00 * (1.0 - wx) * (1.0 - wy)
        + c10 * wx * (1.0 - wy)
        + c01 * (1.0 - wx) * wy
        + c11 * wx * wy
    )
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def slice_equirectangular(
    panorama_bytes: bytes,
    *,
    count: int = 10,
    hfov_deg: float = 90.0,
    width: int = 1280,
    height: int = 720,
    plan: list[tuple[float, float]] | None = None,
    jpeg_quality: int = 92,
) -> list[PanoSlice]:
    """Slice an equirectangular panorama into overlapping pinhole JPEG crops."""
    if not panorama_bytes:
        raise ValueError("panorama_bytes is empty")
    views = plan if plan is not None else default_slice_plan(count)
    if not (8 <= len(views) <= 12):
        raise ValueError(f"Expected 8 to 12 slices, got {len(views)}")

    with Image.open(io.BytesIO(panorama_bytes)) as img:
        rgb = img.convert("RGB")
        eq_arr = np.asarray(rgb, dtype=np.uint8)

    if eq_arr.ndim != 3 or eq_arr.shape[0] < 32 or eq_arr.shape[1] < 64:
        raise ValueError("Panorama image is too small to slice")

    slices: list[PanoSlice] = []
    for idx, (yaw_deg, pitch_deg) in enumerate(views):
        rot, camera = pinhole_camera_dict(
            yaw_deg,
            pitch_deg,
            width=width,
            height=height,
            hfov_deg=hfov_deg,
        )
        crop_arr = _sample_equirect(eq_arr, rot, camera)
        buf = io.BytesIO()
        Image.fromarray(crop_arr, mode="RGB").save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        slices.append(
            PanoSlice(
                index=idx,
                name=f"slice_{idx:02d}_y{int(round(yaw_deg))}_p{int(round(pitch_deg))}.jpg",
                yaw_deg=float(yaw_deg),
                pitch_deg=float(pitch_deg),
                hfov_deg=float(hfov_deg),
                width=width,
                height=height,
                image_bytes=buf.getvalue(),
                mime_type="image/jpeg",
                camera=camera,
            )
        )
    return slices

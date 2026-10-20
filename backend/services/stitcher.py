"""Stitch a guided 360° capture into a 2:1 equirectangular panorama.

The phone takes 16 ultra-wide photos at known yaw steps. OpenCV's global
Stitcher is a poor fit for that capture: bundle adjustment fails on the full
loop (CAMERA_PARAMS_ADJUST_FAIL), and a successful planar stitch is still not
equirectangular. Chaining homographies is worse — error compounds around the
circle.

This pipeline instead projects each photo onto a shared equirectangular canvas
with a pinhole camera at the captured yaw. Overlaps are feather-blended, the
loop is 360° by construction. A ring of ultra-wide photos does not cover
the zenith or nadir, so those caps are inpainted in pole-centered images
(no black poles) and the left/right edges are sealed so the equirect wraps.
The JPEG is exactly 2:1, which World Labs expects for ``is_pano``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Longest side before projection. Full-resolution phone photos make feature
# matching ambiguous and blow up the equirectangular canvas.
MAX_INPUT_DIM = 1500

# Output width is 2π * focal, clamped so a Railway-sized instance stays
# within memory. Height is always exactly width / 2.
MIN_OUTPUT_WIDTH = 640
MAX_OUTPUT_WIDTH = 4096

# Portrait ultra-wide on iPhone 11+ is about 120° on the diagonal. Held in
# portrait (4:3), the horizontal field is ~92°. Override via hfov_deg when the
# capture used a different lens.
DEFAULT_HFOV_DEG = 92.0

_OFFSET_SEARCH_FEATURES = 2500


def stitch_images(
    image_paths: list[Path],
    output_path: Path,
    *,
    yaws_deg: list[float] | None = None,
    hfov_deg: float | None = None,
    refine: bool = True,
    pitches_deg: list[float] | None = None,
) -> bool:
    """Stitch ordered photos into an equirectangular JPEG.

    ``yaws_deg`` is the compass yaw of each photo in degrees (0 = first shot,
    increasing as the user turns). When omitted, shots are spaced evenly
    around the circle. ``hfov_deg`` is the horizontal field of view of the
    photos; EXIF is tried next, then the portrait ultra-wide default.
    """
    paths = sorted(image_paths, key=lambda p: p.name)
    images = _load_images(paths)
    if len(images) < 2:
        logger.error("Need at least 2 images to stitch, got %d", len(images))
        return False

    images = _downscale_images(images)
    images = _match_sizes(images)
    hfov = _resolve_hfov(hfov_deg, _hfov_from_exif(paths[0]))
    yaws = _resolve_yaws(yaws_deg, len(images))
    captured_pitches = _resolve_pitches(pitches_deg, len(images))
    pitches = list(captured_pitches) if captured_pitches else [0.0] * len(images)
    logger.info(
        "Stitch input: %d images %dx%d, hfov=%.1f° (sent=%s), yaws=%s, pitches=%s",
        len(images),
        images[0].shape[1],
        images[0].shape[0],
        hfov,
        hfov_deg,
        [round(y, 1) for y in yaws],
        [round(p, 1) for p in pitches],
    )

    if refine:
        refined_yaws, refined_pitches = _refine_alignment(images, yaws, hfov)
        if refined_yaws is not None:
            yaws = refined_yaws
            # Measured pitch from the phone beats image-derived drift.
            if not captured_pitches:
                pitches = refined_pitches or pitches

    panorama = _project_equirect(images, yaws, pitches, hfov)
    if panorama is None:
        logger.error("Equirectangular projection produced an empty canvas")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), panorama, [cv2.IMWRITE_JPEG_QUALITY, 95]):
        logger.error("Failed to write panorama to %s", output_path)
        return False

    height, width = panorama.shape[:2]
    logger.info(
        "Panorama saved to %s (%dx%d, hfov=%.1f°, 2:1=%s)",
        output_path,
        width,
        height,
        hfov,
        width == height * 2,
    )
    return True


def render_perspective(
    equirect_bgr: np.ndarray,
    yaw_deg: float,
    hfov_deg: float,
    out_w: int,
    out_h: int,
    pitch_deg: float = 0.0,
) -> np.ndarray:
    """Rasterize one pinhole view of an equirectangular image.

    Used by the synthetic stitch test and kept in this module so the capture
    model and the stitcher stay inverses of each other. The center pixel looks
    along ``yaw_deg`` at ``pitch_deg`` (0 = horizon, positive = up).
    """
    src_h, src_w = equirect_bgr.shape[:2]
    hfov = np.radians(hfov_deg)
    focal = (out_w / 2.0) / np.tan(hfov / 2.0)
    cx = (out_w - 1) / 2.0
    cy = (out_h - 1) / 2.0
    yaw = np.radians(yaw_deg)
    pitch = np.radians(pitch_deg)
    cos_p, sin_p = np.cos(pitch), np.sin(pitch)
    cos_y, sin_y = np.cos(yaw), np.sin(yaw)

    xs = np.arange(out_w, dtype=np.float32)
    ys = np.arange(out_h, dtype=np.float32)
    uu, vv = np.meshgrid(xs, ys)
    x2 = (uu - cx) / focal
    y2 = -(vv - cy) / focal
    z2 = np.ones_like(x2)

    y1 = y2 * cos_p + z2 * sin_p
    z1 = -y2 * sin_p + z2 * cos_p
    x1 = x2
    world_x = x1 * cos_y + z1 * sin_y
    world_y = y1
    world_z = -x1 * sin_y + z1 * cos_y

    lon = np.arctan2(world_x, world_z)
    lat = np.arctan2(world_y, np.hypot(world_x, world_z))
    map_x = ((lon + np.pi) / (2 * np.pi) * src_w).astype(np.float32)
    map_y = ((np.pi / 2 - lat) / np.pi * src_h).astype(np.float32)
    return cv2.remap(
        equirect_bgr,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_WRAP,
    )


def _load_images(paths: list[Path]) -> list[np.ndarray]:
    images: list[np.ndarray] = []
    for path in paths:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            logger.warning("Failed to read image: %s", path)
            continue
        images.append(image)
    return images


def _downscale_images(images: list[np.ndarray]) -> list[np.ndarray]:
    out = []
    for image in images:
        height, width = image.shape[:2]
        long_side = max(height, width)
        if long_side > MAX_INPUT_DIM:
            scale = MAX_INPUT_DIM / long_side
            image = cv2.resize(
                image,
                (max(2, int(round(width * scale))), max(2, int(round(height * scale)))),
                interpolation=cv2.INTER_AREA,
            )
        out.append(image)
    return out


def _match_sizes(images: list[np.ndarray]) -> list[np.ndarray]:
    height, width = images[0].shape[:2]
    matched = [images[0]]
    for image in images[1:]:
        if image.shape[:2] != (height, width):
            image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        matched.append(image)
    return matched


def _hfov_from_exif(path: Path) -> float | None:
    """Horizontal FOV from the 35mm-equivalent focal length, if EXIF has it."""
    try:
        from PIL import Image

        with Image.open(path) as handle:
            from services.uploads import focal_35mm_from_image

            focal_35 = focal_35mm_from_image(handle)
            if not focal_35:
                return None
            width, height = handle.size
    except Exception:
        logger.debug("EXIF hfov lookup failed for %s", path, exc_info=True)
        return None

    focal_35 = float(focal_35)
    if focal_35 <= 0:
        return None
    # 35mm-equivalent focal length matches the diagonal of a 36×24mm frame.
    diag_fov = 2 * np.arctan(43.266 / (2 * focal_35))
    diag = float(np.hypot(width, height))
    hfov = 2 * np.arctan((width / diag) * np.tan(diag_fov / 2))
    return float(np.degrees(hfov))


# Sent and EXIF field of view may disagree by this much before EXIF wins.
HFOV_EXIF_TOLERANCE_DEG = 8.0


def _resolve_hfov(sent: float | None, exif: float | None) -> float:
    """Prefer the app's lens FOV, but trust EXIF when they clearly disagree.

    The app knows which lens it selected; EXIF knows which lens actually took
    the photo. A mismatch (Expo Go falling back to the wide lens, say) wrecks
    the projection, so the photo's own metadata wins and the mismatch is logged.
    """
    if sent and exif:
        if abs(float(sent) - exif) > HFOV_EXIF_TOLERANCE_DEG:
            logger.warning("hfov mismatch: app sent %.1f°, EXIF says %.1f°; using EXIF", float(sent), exif)
            return float(exif)
        return float(sent)
    if sent:
        return float(sent)
    if exif:
        return float(exif)
    logger.warning("No hfov from app or EXIF; using default %.1f°", DEFAULT_HFOV_DEG)
    return DEFAULT_HFOV_DEG


def _resolve_pitches(pitches_deg: list[float] | None, count: int) -> list[float] | None:
    if not pitches_deg:
        return None
    if len(pitches_deg) != count:
        logger.warning("Ignoring %d pitch values for %d images", len(pitches_deg), count)
        return None
    return [max(-45.0, min(45.0, float(p))) for p in pitches_deg]


def _resolve_yaws(yaws_deg: list[float] | None, count: int) -> list[float]:
    if yaws_deg and len(yaws_deg) == count:
        return [float(yaw) % 360.0 for yaw in yaws_deg]
    if yaws_deg and len(yaws_deg) != count:
        logger.warning(
            "Ignoring %d yaw values for %d images; using even spacing",
            len(yaws_deg),
            count,
        )
    step = 360.0 / count
    return [i * step for i in range(count)]


def _refine_alignment(
    images: list[np.ndarray],
    yaws: list[float],
    hfov_deg: float,
) -> tuple[list[float] | None, list[float] | None]:
    """Nudge the nominal yaws using translation measured on a cylinder.

    Measurements are accepted only when every neighbor matches and the steps
    close the loop near 360°. Otherwise the captured (or evenly spaced) yaws
    are kept — a bad match must not spin the panorama.
    """
    height, width = images[0].shape[:2]
    focal = (width / 2.0) / np.tan(np.radians(hfov_deg) / 2.0)
    warped = [_cylindrical_warp(image, focal) for image in images]
    dxs: list[float] = []
    dys: list[float] = []
    count = len(warped)
    for index in range(count):
        offset = _estimate_offset(warped[index], warped[(index + 1) % count])
        if offset is None:
            logger.info("Alignment refine skipped: pair %d did not match", index)
            return None, None
        dxs.append(float(offset[0]))
        dys.append(float(offset[1]))

    if any(dx <= width * 0.04 for dx in dxs):
        logger.info("Alignment refine skipped: a step was not a forward rotation")
        return None, None

    circumference = 2 * np.pi * focal
    total = float(sum(dxs))
    if total <= 0 or abs(total - circumference) / circumference > 0.12:
        logger.info(
            "Alignment refine skipped: loop is %.0fpx vs circumference %.0fpx",
            total,
            circumference,
        )
        return None, None

    median_step = float(np.median(dxs))
    if any(abs(dx - median_step) > 0.45 * median_step for dx in dxs):
        logger.info("Alignment refine skipped: step sizes disagree")
        return None, None

    scale = circumference / total
    yaws_out = [yaws[0]]
    for dx in dxs[:-1]:
        yaws_out.append((yaws_out[-1] + np.degrees(dx * scale / focal)) % 360.0)

    # Vertical drift around a level loop should sum to ~0. Convert the
    # closed residual into a small per-shot pitch, and drop it if it is large
    # enough to be a bad match rather than handheld tilt.
    dy = np.array(dys, dtype=np.float64)
    dy = dy - dy.mean()
    pitches = [0.0]
    for step in dy[:-1]:
        pitches.append(pitches[-1] + float(np.degrees(step / focal)))
    if max(abs(p) for p in pitches) > 5:
        pitches = [0.0] * count
    logger.info("Alignment refine accepted (scale %.3f)", scale)
    return yaws_out, pitches


def _cylindrical_warp(image: np.ndarray, focal: float) -> np.ndarray:
    """Map a pinhole photo onto a cylinder so pure yaw becomes translation."""
    height, width = image.shape[:2]
    cx, cy = width / 2.0, height / 2.0
    u = np.arange(width, dtype=np.float32)
    v = np.arange(height, dtype=np.float32)
    uu, vv = np.meshgrid(u, v)
    theta = (uu - cx) / focal
    height_param = (vv - cy) / focal
    x = np.sin(theta)
    y = height_param
    z = np.cos(theta)
    map_x = (focal * x / z + cx).astype(np.float32)
    map_y = (focal * y / z + cy).astype(np.float32)
    return cv2.remap(
        image,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )


def _estimate_offset(image_a: np.ndarray, image_b: np.ndarray) -> tuple[int, int] | None:
    """Return (dx, dy) placing image_b relative to image_a. None if unsure."""
    orb = cv2.ORB_create(nfeatures=_OFFSET_SEARCH_FEATURES)
    gray_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2GRAY)
    keypoints_a, desc_a = orb.detectAndCompute(gray_a, None)
    keypoints_b, desc_b = orb.detectAndCompute(gray_b, None)
    if desc_a is None or desc_b is None or len(keypoints_a) < 20 or len(keypoints_b) < 20:
        return None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = matcher.match(desc_a, desc_b)
    if len(matches) < 20:
        return None
    matches = sorted(matches, key=lambda item: item.distance)[:120]
    dxs = np.array(
        [keypoints_a[m.queryIdx].pt[0] - keypoints_b[m.trainIdx].pt[0] for m in matches]
    )
    dys = np.array(
        [keypoints_a[m.queryIdx].pt[1] - keypoints_b[m.trainIdx].pt[1] for m in matches]
    )
    median_dx, median_dy = np.median(dxs), np.median(dys)
    distance = np.sqrt((dxs - median_dx) ** 2 + (dys - median_dy) ** 2)
    mad = np.median(distance) + 1.0
    inliers = distance < 3 * mad
    if int(inliers.sum()) < 12:
        return None
    return int(round(float(np.median(dxs[inliers])))), int(round(float(np.median(dys[inliers]))))


def _project_equirect(
    images: list[np.ndarray],
    yaws: list[float],
    pitches: list[float],
    hfov_deg: float,
) -> np.ndarray | None:
    height, width = images[0].shape[:2]
    hfov = np.radians(hfov_deg)
    focal = (width / 2.0) / np.tan(hfov / 2.0)
    out_w = int(round(2 * np.pi * focal))
    out_w = max(MIN_OUTPUT_WIDTH, min(MAX_OUTPUT_WIDTH, out_w))
    out_w -= out_w % 2
    out_h = out_w // 2

    # When the focal length wants a wider canvas than we allow, sample the
    # output on that coarser grid but keep the source focal — the pinhole
    # model is in source pixels, and the equirect maps longitude linearly.
    scales = _overlap_gains(images, yaws, hfov_deg, pitches)
    acc = np.zeros((out_h, out_w, 3), dtype=np.float32)
    weight_acc = np.zeros((out_h, out_w), dtype=np.float32)
    for image, yaw, pitch, scale in zip(images, yaws, pitches, scales):
        prepared = np.clip(image.astype(np.float32) * scale, 0, 255)
        _blend_image(prepared, acc, weight_acc, yaw, pitch, hfov_deg, focal)

    valid = weight_acc > 1e-9
    if int(valid.sum()) < 100:
        return None
    composed = np.zeros_like(acc)
    composed[valid] = acc[valid] / weight_acc[valid, None]
    panorama = _fill_poles(composed, valid)
    panorama = _seal_seam(panorama)
    return np.clip(np.round(panorama), 0, 255).astype(np.uint8)


def _overlap_gains(
    images: list[np.ndarray],
    yaws: list[float],
    hfov_deg: float,
    pitches: list[float] | None = None,
) -> list[float]:
    """Match exposure where neighboring photos overlap on the sphere.

    Each photo is projected onto a small equirect canvas with its real yaw and
    pitch, and neighbors are compared only on pixels both cover. The earlier
    version compared fixed image-edge strips, which assumes exact 22.5° steps
    and no tilt; with handheld yaw jitter it compared different content and
    produced bright/dark bands. Scales stay at 1 when shots already agree.
    """
    count = len(images)
    ones = [1.0] * count
    if count < 2:
        return ones
    pitches = pitches or [0.0] * count
    small_w = 512
    small_h = small_w // 2
    height, width = images[0].shape[:2]
    scale = min(1.0, 320.0 / max(height, width))
    thumbs = [
        cv2.cvtColor(
            cv2.resize(image, (max(2, int(width * scale)), max(2, int(height * scale))), interpolation=cv2.INTER_AREA),
            cv2.COLOR_BGR2GRAY,
        ).astype(np.float32)[..., None]
        for image in images
    ]
    t_h, t_w = thumbs[0].shape[:2]
    focal = (t_w / 2.0) / np.tan(np.radians(hfov_deg) / 2.0)
    projected: list[tuple[np.ndarray, np.ndarray]] = []
    for thumb, yaw, pitch in zip(thumbs, yaws, pitches):
        acc = np.zeros((small_h, small_w, 1), dtype=np.float32)
        weight = np.zeros((small_h, small_w), dtype=np.float32)
        _blend_image(thumb, acc, weight, yaw, pitch, hfov_deg, focal, center_power=0.0)
        mask = weight > 0.2
        values = np.zeros((small_h, small_w), dtype=np.float32)
        values[mask] = acc[..., 0][mask] / weight[mask]
        projected.append((values, mask))

    ratios: list[float] = []
    for index in range(count):
        values_a, mask_a = projected[index]
        values_b, mask_b = projected[(index + 1) % count]
        shared = mask_a & mask_b
        if int(shared.sum()) < 50:
            return ones
        ratios.append((float(np.median(values_a[shared])) + 1.0) / (float(np.median(values_b[shared])) + 1.0))

    logs = np.log(np.clip(ratios, 0.5, 2.0))
    logs = logs - logs.mean()
    if float(np.max(np.abs(logs))) < 0.02:
        return ones
    log_scale = np.zeros(count, dtype=np.float64)
    for index in range(count - 1):
        log_scale[index + 1] = log_scale[index] + logs[index]
    log_scale -= log_scale.mean()
    scales = np.exp(np.clip(log_scale, np.log(0.75), np.log(1.35)))
    return [float(scale) for scale in scales]


def _blend_image(
    image: np.ndarray,
    acc: np.ndarray,
    weight_acc: np.ndarray,
    yaw_deg: float,
    pitch_deg: float,
    hfov_deg: float,
    focal: float,
    center_power: float | None = None,
) -> None:
    src_h, src_w = image.shape[:2]
    out_h, out_w = weight_acc.shape
    hfov = np.radians(hfov_deg)
    vfov = 2 * np.arctan((src_h / float(src_w)) * np.tan(hfov / 2.0))
    margin = np.radians(1.2)
    half_lon = hfov / 2.0 + margin
    half_lat = vfov / 2.0 + margin
    yaw = np.radians(yaw_deg)
    pitch = np.radians(pitch_deg)
    cos_p, sin_p = float(np.cos(pitch)), float(np.sin(pitch))
    cos_y, sin_y = float(np.cos(yaw)), float(np.sin(yaw))

    span = (2 * half_lon) / (2 * np.pi) * out_w
    x_left = np.mod(((yaw - half_lon) + np.pi) / (2 * np.pi) * out_w, out_w)
    if x_left + span <= out_w:
        segments = [(float(x_left), float(x_left + span))]
    else:
        segments = [
            (float(x_left), float(out_w)),
            (0.0, float(x_left + span - out_w)),
        ]

    y_top = (np.pi / 2 - half_lat) / np.pi * out_h
    y_bot = (np.pi / 2 + half_lat) / np.pi * out_h
    y0 = max(0, int(np.floor(y_top)) - 1)
    y1 = min(out_h, int(np.ceil(y_bot)) + 1)
    if y1 <= y0:
        return

    cx = (src_w - 1) / 2.0
    cy = (src_h - 1) / 2.0
    rows = np.arange(y0, y1)
    lat = np.pi / 2 - (rows + 0.5) / out_h * np.pi

    for seg_left, seg_right in segments:
        x0 = max(0, int(np.floor(seg_left)) - 1)
        x1 = min(out_w, int(np.ceil(seg_right)) + 1)
        if x1 <= x0:
            continue
        cols = np.arange(x0, x1)
        lon = (cols + 0.5) / out_w * 2 * np.pi - np.pi
        lon_grid, lat_grid = np.meshgrid(lon, lat)
        cos_lat = np.cos(lat_grid)
        world_x = np.sin(lon_grid) * cos_lat
        world_y = np.sin(lat_grid)
        world_z = np.cos(lon_grid) * cos_lat
        x1c = world_x * cos_y - world_z * sin_y
        y1c = world_y
        z1c = world_x * sin_y + world_z * cos_y
        y2 = y1c * cos_p - z1c * sin_p
        z2 = y1c * sin_p + z1c * cos_p
        safe = z2 > 0.05
        z_safe = np.where(safe, z2, 1.0)
        map_x = (cx + focal * (x1c / z_safe)).astype(np.float32)
        map_y = (cy - focal * (y2 / z_safe)).astype(np.float32)
        patch = cv2.remap(
            image,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        inside = safe & (map_x >= 0) & (map_y >= 0) & (map_x <= src_w - 1) & (map_y <= src_h - 1)
        weight = _feather(map_x, map_y, src_w, src_h) * inside.astype(np.float32)
        power = CENTER_WEIGHT_POWER if center_power is None else center_power
        if power > 0:
            weight = weight * _center_weight(map_x, src_w, power)
        if patch.ndim == 2:
            patch = patch[..., None]
        acc[y0:y1, x0:x1] += patch * weight[..., None]
        weight_acc[y0:y1, x0:x1] += weight


# Exponent on the horizontal center weight. Large values give every output
# pixel to the photo whose center is nearest, with a narrow cross-fade at the
# seam, instead of averaging all ~4 overlapping photos (ghosting).
CENTER_WEIGHT_POWER = 12.0


def _center_weight(map_x: np.ndarray, width: int, power: float) -> np.ndarray:
    half = max((width - 1) / 2.0, 1.0)
    offset = np.clip(np.abs(map_x - half) / half, 0.0, 1.0)
    return np.power(1.0 - offset, power).astype(np.float32) + 1e-6


def _feather(map_x: np.ndarray, map_y: np.ndarray, width: int, height: int) -> np.ndarray:
    """Smoothstep weight that is 1 at the photo center and 0 at its edges."""
    edge_x = max(width * 0.08, 2.0)
    edge_y = max(height * 0.08, 2.0)
    nx = np.minimum(map_x / edge_x, (width - 1 - map_x) / edge_x)
    ny = np.minimum(map_y / edge_y, (height - 1 - map_y) / edge_y)
    t = np.clip(np.minimum(nx, ny), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def _fill_poles(composed: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Inpaint the zenith and nadir, which a single photo ring does not see.

    Phone ultra-wide shots cover roughly ±50° of latitude, so the equirect
    poles would otherwise stay black. Each cap is filled in a square centered
    on the pole, where the ceiling or floor is an ordinary disk instead of a
    pinched equirectangular smear, then written back. Left and right edges
    meet because that disk has no longitude cut.
    """
    filled = composed.copy()
    _inpaint_pole_cap(filled, valid, zenith=True)
    _fill_nadir(filled, valid)
    return _inpaint_remaining_holes(filled, valid)


def _fill_nadir(image: np.ndarray, valid: np.ndarray) -> None:
    """Fill the unseen floor without blending over the photographed rows.

    A wide inpaint feather mixes the cap into the last real floor rows and
    reads as a blurry band. Those rows stay untouched. The hole takes the
    color of the floor edge at each longitude, then eases to that edge's
    average toward the pole so the cap is even instead of a stretched smear.
    """
    height, width = image.shape[:2]
    covered = np.where(valid.mean(axis=1) > 0.2)[0]
    if len(covered) == 0:
        return
    boundary = int(covered[-1])
    if boundary >= height - 2:
        return
    ring_start = max(0, boundary - 4)
    ring_valid = valid[ring_start : boundary + 1]
    if not bool(ring_valid.any()):
        return
    ring_h = ring_valid.shape[0]
    row_index = np.where(ring_valid, np.arange(ring_h)[:, None], -1).max(axis=0)
    known = row_index >= 0
    if not bool(known.any()):
        return
    edge = np.zeros((width, 3), dtype=np.float32)
    cols = np.flatnonzero(known)
    edge[cols] = image[ring_start + row_index[cols], cols]
    unknown = np.flatnonzero(~known)
    if len(unknown):
        delta = np.abs(unknown[:, None] - cols[None, :])
        delta = np.minimum(delta, width - delta)
        edge[unknown] = edge[cols[delta.argmin(axis=1)]]
    mean = edge.mean(axis=0)
    start = boundary + 1
    span = height - start
    offsets = np.arange(span, dtype=np.float32)
    ease = float(min(6, span))
    blend = np.clip(offsets / ease, 0.0, 1.0)[:, None, None]
    colors = edge[None, :, :] * (1.0 - blend) + mean.reshape(1, 1, 3) * blend
    image[start:] = colors


def _inpaint_pole_cap(image: np.ndarray, valid: np.ndarray, *, zenith: bool) -> None:
    height, width = image.shape[:2]
    row_fraction = valid.mean(axis=1)
    covered = np.where(row_fraction > 0.2)[0]
    if len(covered) == 0:
        return
    if zenith:
        boundary = int(covered[0])
        if boundary <= 1:
            return
        lat_bound = np.pi / 2 - (boundary + 0.5) / height * np.pi
        cap_radius = float(np.pi / 2 - lat_bound + np.radians(8))
        row_limit = min(height, int(np.ceil(cap_radius / np.pi * height)) + 2)
        rows = np.arange(0, row_limit)
        lat = np.pi / 2 - (rows + 0.5) / height * np.pi
    else:
        boundary = int(covered[-1])
        if boundary >= height - 2:
            return
        lat_bound = np.pi / 2 - (boundary + 0.5) / height * np.pi
        cap_radius = float(lat_bound + np.pi / 2 + np.radians(8))
        row_start = max(0, int(np.floor((np.pi - cap_radius) / np.pi * height)) - 2)
        rows = np.arange(row_start, height)
        lat = np.pi / 2 - (rows + 0.5) / height * np.pi
    cap_radius = max(cap_radius, np.radians(4))
    size = 192
    cap, hole = _render_cap(image, valid, cap_radius, zenith=zenith, size=size)
    if int(hole.sum()) < 8:
        return
    painted = _inpaint_uint8(cap, hole)
    cols = np.arange(width)
    lon = (cols + 0.5) / width * 2 * np.pi - np.pi
    lon_grid, lat_grid = np.meshgrid(lon, lat)
    ang = (np.pi / 2 - lat_grid) if zenith else (lat_grid + np.pi / 2)
    radius = np.clip(ang / cap_radius, 0, 1.25)
    hypot = radius / 2.0
    nx = np.sin(lon_grid) * hypot
    ny = -np.cos(lon_grid) * hypot
    map_x = ((nx + 0.5) * size - 0.5).astype(np.float32)
    map_y = ((ny + 0.5) * size - 0.5).astype(np.float32)
    sampled = cv2.remap(
        painted,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    ).astype(np.float32)
    region_valid = valid[rows[0] : rows[-1] + 1]
    hole_eq = ((~region_valid) & (radius <= 1.0)).astype(np.float32)
    alpha = _blur_wrap_x(hole_eq, 3.5)
    alpha = alpha[..., None]
    region = image[rows[0] : rows[-1] + 1]
    image[rows[0] : rows[-1] + 1] = region * (1.0 - alpha) + sampled * alpha


def _render_cap(
    image: np.ndarray,
    valid: np.ndarray,
    cap_radius: float,
    *,
    zenith: bool,
    size: int,
) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.mgrid[0:size, 0:size]
    nx = (xs + 0.5) / size - 0.5
    ny = (ys + 0.5) / size - 0.5
    radius = np.hypot(nx, ny) * 2.0
    ang = radius * cap_radius
    lon = np.arctan2(nx, -ny)
    lat = (np.pi / 2 - ang) if zenith else (-np.pi / 2 + ang)
    cap = _sample_equirect(image, lon, lat)
    known = _sample_equirect(valid.astype(np.float32), lon, lat) > 0.5
    hole = (radius <= 1.0) & ~known
    return cap, hole


def _sample_equirect(image: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    map_x = np.mod((lon + np.pi) / (2 * np.pi) * width, width).astype(np.float32)
    map_y = np.clip((np.pi / 2 - lat) / np.pi * height, 0, height - 1).astype(np.float32)
    source = image.astype(np.float32) if image.dtype != np.float32 else image
    return cv2.remap(
        source,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_WRAP,
    )


def _inpaint_uint8(image: np.ndarray, hole: np.ndarray) -> np.ndarray:
    rgb = np.clip(np.round(image), 0, 255).astype(np.uint8)
    mask = hole.astype(np.uint8) * 255
    if int(mask.sum()) == 0:
        return rgb
    try:
        return cv2.inpaint(rgb, mask, 7, cv2.INPAINT_TELEA)
    except cv2.error:
        logger.warning("Pole inpaint failed; using a blurred cap")
        blurred = cv2.GaussianBlur(rgb, (0, 0), 12)
        rgb[hole] = blurred[hole]
        return rgb


def _inpaint_remaining_holes(image: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Fill any pixels the caps missed, wrapping so the seam is not a wall."""
    hole = ~valid
    if not bool(hole.any()):
        return image
    # Caps already covered the poles. Ignore pixels whose row is mostly empty;
    # those belong to the caps. Remaining holes are gaps inside the band.
    row_fraction = valid.mean(axis=1)
    band = row_fraction > 0.2
    hole = hole & band[:, None]
    if not bool(hole.any()):
        return image
    pad = 24
    rgb = np.clip(np.round(image), 0, 255).astype(np.uint8)
    mask = hole.astype(np.uint8) * 255
    padded = np.concatenate([rgb[:, -pad:], rgb, rgb[:, :pad]], axis=1)
    mask_pad = np.concatenate([mask[:, -pad:], mask, mask[:, :pad]], axis=1)
    try:
        painted = cv2.inpaint(padded, mask_pad, 5, cv2.INPAINT_TELEA)
    except cv2.error:
        return image
    cropped = painted[:, pad:-pad].astype(np.float32)
    out = image.copy()
    out[hole] = cropped[hole]
    return out


def _blur_wrap_x(image: np.ndarray, sigma: float) -> np.ndarray:
    pad = int(sigma * 3) + 1
    padded = np.concatenate([image[:, -pad:], image, image[:, :pad]], axis=1)
    blurred = cv2.GaussianBlur(padded, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return blurred[:, pad:-pad]


def _seal_seam(image: np.ndarray, width: int = 16) -> np.ndarray:
    """Spread any left/right step so column 0 and the last column match.

    Marble treats a 2:1 image as a panorama only when those edges meet.
    A zero step is left unchanged.
    """
    height, width_px = image.shape[:2]
    width = min(width, max(2, width_px // 8))
    if width_px < width * 2 or height == 0:
        return image
    out = image.copy()
    delta = out[:, 0] - out[:, -1]
    fade = np.linspace(1.0, 0.0, width, endpoint=False, dtype=np.float32)
    out[:, :width] -= delta[:, None, :] * fade[None, :, None] * 0.5
    out[:, -width:] += delta[:, None, :] * fade[::-1][None, :, None] * 0.5
    return out

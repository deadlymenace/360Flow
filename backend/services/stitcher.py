import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Resize inputs so the longest side is at most this many pixels before stitching.
# Full-resolution iPhone photos (~12MP) make OpenCV's bundle adjustment fail and
# slow ORB matching to a crawl. 1500px preserves plenty of detail for a 360° viewer.
_MAX_INPUT_DIM = 1500

# Assumed horizontal FOV of an iPhone's main (wide) back camera.
# Used to estimate focal length for the cylindrical fallback.
_IPHONE_HFOV_DEG = 70.0

# Sanity caps on the cylindrical canvas to reject runaway alignments.
_MAX_CANVAS_W = 30000
_MAX_CANVAS_H = 5000


def _downscale_images(images: list[np.ndarray]) -> list[np.ndarray]:
    """Shrink images so their longest side is _MAX_INPUT_DIM, preserving aspect ratio."""
    out = []
    for img in images:
        h, w = img.shape[:2]
        long_side = max(h, w)
        if long_side > _MAX_INPUT_DIM:
            scale = _MAX_INPUT_DIM / long_side
            img = cv2.resize(
                img,
                (int(round(w * scale)), int(round(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        out.append(img)
    return out


def _enhance_images(images: list[np.ndarray]) -> list[np.ndarray]:
    """Apply CLAHE contrast enhancement to help the stitcher find features
    in low-texture scenes (plain walls, uniform floors, etc.)."""
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = []
    for img in images:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        enhanced.append(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR))
    return enhanced


def _crop_black_borders(panorama: np.ndarray) -> np.ndarray:
    """Remove black borders produced by warping."""
    gray = cv2.cvtColor(panorama, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
        panorama = panorama[y : y + h, x : x + w]
    return panorama


def _try_opencv_stitcher(images: list[np.ndarray]) -> np.ndarray | None:
    """Primary attempt: OpenCV's built-in Stitcher (global bundle adjustment)."""
    stitcher = cv2.Stitcher.create(cv2.Stitcher_PANORAMA)
    stitcher.setPanoConfidenceThresh(0.3)

    # Try with a duplicate of the first image appended to help close the 360° loop
    images_wrapped = images + [images[0]]
    status, panorama = stitcher.stitch(images_wrapped)
    if status == cv2.Stitcher_OK:
        return panorama

    logger.warning("Wrapped stitch failed (status %d), trying without wrap...", status)
    stitcher2 = cv2.Stitcher.create(cv2.Stitcher_PANORAMA)
    stitcher2.setPanoConfidenceThresh(0.3)
    status, panorama = stitcher2.stitch(images)
    if status == cv2.Stitcher_OK:
        return panorama

    status_names = {
        cv2.Stitcher_ERR_NEED_MORE_IMGS: "NEED_MORE_IMGS",
        cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL: "HOMOGRAPHY_EST_FAIL",
        cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL: "CAMERA_PARAMS_ADJUST_FAIL",
    }
    logger.warning("OpenCV stitcher failed: %s", status_names.get(status, str(status)))
    return None


def _cylindrical_warp(img: np.ndarray, focal: float) -> np.ndarray:
    """Project a pinhole image onto a cylinder with the given focal length (in pixels).
    The camera sits on the cylinder's axis with the principal point at image center.
    This is the correct projection model for rotational 360° captures — it flattens
    the cylinder into a strip where rotation becomes pure horizontal translation."""
    h, w = img.shape[:2]
    cx, cy = w / 2.0, h / 2.0

    u = np.arange(w, dtype=np.float32)
    v = np.arange(h, dtype=np.float32)
    U, V = np.meshgrid(u, v)

    # Inverse map: for each pixel on the cylinder, find where to sample in the source.
    # theta = horizontal angle on the cylinder; h_param = vertical coord
    theta = (U - cx) / focal
    h_param = (V - cy) / focal

    # 3D ray from the camera center
    X = np.sin(theta)
    Y = h_param
    Z = np.cos(theta)

    # Project back onto the original image plane
    x_src = (focal * X / Z + cx).astype(np.float32)
    y_src = (focal * Y / Z + cy).astype(np.float32)

    return cv2.remap(
        img,
        x_src,
        y_src,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )


def _estimate_offset(img_a: np.ndarray, img_b: np.ndarray) -> tuple[int, int] | None:
    """Find (dx, dy) such that placing img_b at (img_a_origin + (dx, dy)) aligns
    matched features. Uses ORB features with median-of-matches for robustness.
    Returns None if there aren't enough confident matches."""
    orb = cv2.ORB_create(nfeatures=2000)
    gray_a = cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(img_b, cv2.COLOR_BGR2GRAY)

    kp_a, des_a = orb.detectAndCompute(gray_a, None)
    kp_b, des_b = orb.detectAndCompute(gray_b, None)
    if des_a is None or des_b is None:
        return None

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(des_a, des_b)
    if len(matches) < 20:
        return None

    matches = sorted(matches, key=lambda m: m.distance)[:100]

    # Shared point P is at (A_origin + a_pt) = (B_origin + b_pt), so:
    #   B_origin = A_origin + (a_pt - b_pt)
    # We want offset = (a_pt - b_pt), median'd for robustness.
    dxs = np.array([kp_a[m.queryIdx].pt[0] - kp_b[m.trainIdx].pt[0] for m in matches])
    dys = np.array([kp_a[m.queryIdx].pt[1] - kp_b[m.trainIdx].pt[1] for m in matches])

    # Drop matches whose offset is a statistical outlier from the median
    med_dx, med_dy = np.median(dxs), np.median(dys)
    dist = np.sqrt((dxs - med_dx) ** 2 + (dys - med_dy) ** 2)
    mad = np.median(dist) + 1.0
    inliers = dist < 3 * mad
    if inliers.sum() < 10:
        return None

    dx = float(np.median(dxs[inliers]))
    dy = float(np.median(dys[inliers]))
    return int(round(dx)), int(round(dy))


def _cylindrical_stitch(images: list[np.ndarray]) -> np.ndarray | None:
    """Fallback stitcher for rotational 360° captures.

    Pipeline:
      1. Warp every image onto a cylinder using estimated focal length.
      2. Find translation-only alignment between each adjacent pair (ORB + median).
      3. Accumulate offsets (sum, not matrix product) — errors don't explode.
      4. Composite into a strip with paint-where-empty blending.
    """
    if len(images) < 2:
        return None

    h_img, w_img = images[0].shape[:2]
    focal = (w_img / 2.0) / np.tan(np.radians(_IPHONE_HFOV_DEG / 2.0))
    logger.info(
        "Cylindrical stitch: warping %d images (size=%dx%d, focal=%.1fpx)",
        len(images), w_img, h_img, focal,
    )

    warped = [_cylindrical_warp(img, focal) for img in images]
    wh, ww = warped[0].shape[:2]

    # Compute cumulative offsets relative to warped[0]
    offsets: list[tuple[int, int]] = [(0, 0)]
    for i in range(len(warped) - 1):
        off = _estimate_offset(warped[i], warped[i + 1])
        if off is None:
            logger.error(
                "Cylindrical stitch: failed to match pair %d→%d (not enough features)",
                i, i + 1,
            )
            return None
        dx, dy = off
        offsets.append((offsets[-1][0] + dx, offsets[-1][1] + dy))

    xs = [o[0] for o in offsets]
    ys = [o[1] for o in offsets]
    x_min, x_max = min(xs), max(xs) + ww
    y_min, y_max = min(ys), max(ys) + wh
    canvas_w = x_max - x_min
    canvas_h = y_max - y_min

    if canvas_w > _MAX_CANVAS_W or canvas_h > _MAX_CANVAS_H:
        logger.error(
            "Cylindrical stitch: canvas too large (%dx%d) — alignment probably bad",
            canvas_w, canvas_h,
        )
        return None

    logger.info("Cylindrical stitch: composing %dx%d canvas", canvas_w, canvas_h)
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    for i, (ox, oy) in enumerate(offsets):
        x = ox - x_min
        y = oy - y_min
        src = warped[i]
        region = canvas[y : y + wh, x : x + ww]
        src_mask = src.max(axis=2) > 0
        empty_mask = region.max(axis=2) == 0
        write_mask = src_mask & empty_mask
        region[write_mask] = src[write_mask]

    return canvas


def stitch_images(image_paths: list[Path], output_path: Path) -> bool:
    """
    Stitch ordered images into a panorama.
    Pipeline: downscale → enhance → OpenCV global stitcher → cylindrical fallback.
    Returns True on success, False on failure.
    """
    raw_images = []
    for path in image_paths:
        img = cv2.imread(str(path))
        if img is None:
            logger.warning("Failed to read image: %s", path)
            continue
        raw_images.append(img)

    if len(raw_images) < 2:
        logger.error("Need at least 2 images to stitch, got %d", len(raw_images))
        return False

    logger.info("Stitching %d images...", len(raw_images))

    # Downscale first so every subsequent step is fast and well-conditioned
    small = _downscale_images(raw_images)

    # Enhance contrast to help feature detection on low-texture surfaces
    images = _enhance_images(small)

    # Try 1: OpenCV global stitcher (best quality when it works)
    panorama = _try_opencv_stitcher(images)

    # Try 2: cylindrical projection fallback (handles rotational 360° captures)
    if panorama is None:
        logger.info("Falling back to cylindrical projection stitching...")
        panorama = _cylindrical_stitch(images)

    if panorama is None:
        logger.error("All stitching methods failed")
        return False

    panorama = _crop_black_borders(panorama)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), panorama, [cv2.IMWRITE_JPEG_QUALITY, 95])
    logger.info("Panorama saved to %s (%dx%d)", output_path, panorama.shape[1], panorama.shape[0])
    return True

import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Minimum number of inlier matches to accept a pairwise homography
_MIN_MATCH_COUNT = 8


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
    """Attempt stitching with OpenCV's built-in Stitcher (global bundle adjustment).
    Returns the panorama or None on failure."""
    stitcher = cv2.Stitcher.create(cv2.Stitcher_PANORAMA)
    stitcher.setPanoConfidenceThresh(0.3)

    # Try with wrapped images first (duplicate of first appended) to close the 360° loop
    images_wrapped = images + [images[0]]
    status, panorama = stitcher.stitch(images_wrapped)
    if status == cv2.Stitcher_OK:
        return panorama

    # Fall back without the duplicate
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


def _sequential_stitch(images: list[np.ndarray]) -> np.ndarray | None:
    """Fallback: stitch by chaining pairwise homographies between adjacent
    images.  Works when the global stitcher fails due to repeating patterns
    or camera-param issues, because we only need to match neighbours and
    we know they're in capture order."""
    sift = cv2.SIFT_create()
    bf = cv2.BFMatcher()

    h_img, w_img = images[0].shape[:2]

    # Detect keypoints/descriptors for every image
    kps_descs = []
    for img in images:
        kp, des = sift.detectAndCompute(img, None)
        kps_descs.append((kp, des))

    # Compute pairwise homographies: H[i] maps image i+1 into image i's frame
    homographies = []
    for i in range(len(images) - 1):
        kp1, des1 = kps_descs[i]
        kp2, des2 = kps_descs[i + 1]

        if des1 is None or des2 is None:
            logger.error("Sequential stitch: no descriptors for pair %d-%d", i, i + 1)
            return None

        matches = bf.knnMatch(des2, des1, k=2)

        # Lowe's ratio test
        good = [m for m, n in matches if m.distance < 0.75 * n.distance]
        if len(good) < _MIN_MATCH_COUNT:
            logger.error(
                "Sequential stitch: only %d matches for pair %d-%d (need %d)",
                len(good), i, i + 1, _MIN_MATCH_COUNT,
            )
            return None

        src_pts = np.float32([kps_descs[i + 1][0][m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([kps_descs[i][0][m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

        H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        if H is None:
            logger.error("Sequential stitch: homography failed for pair %d-%d", i, i + 1)
            return None

        homographies.append(H)

    # Chain homographies: cumulative[i] maps image i into image 0's frame
    cumulative = [np.eye(3, dtype=np.float64)]
    for H in homographies:
        cumulative.append(cumulative[-1] @ H)

    # Determine output canvas size by transforming all image corners
    all_corners = []
    for i, H_cum in enumerate(cumulative):
        corners = np.float32([
            [0, 0], [w_img, 0], [w_img, h_img], [0, h_img],
        ]).reshape(-1, 1, 2)
        warped = cv2.perspectiveTransform(corners, H_cum)
        all_corners.append(warped)

    all_pts = np.concatenate(all_corners, axis=0)
    x_min, y_min = np.int32(all_pts.min(axis=0).ravel())
    x_max, y_max = np.int32(all_pts.max(axis=0).ravel())

    # Translation to shift everything into positive coordinates
    shift = np.array([
        [1, 0, -x_min],
        [0, 1, -y_min],
        [0, 0, 1],
    ], dtype=np.float64)

    canvas_w = x_max - x_min
    canvas_h = y_max - y_min

    # Cap canvas size to prevent memory blowup
    if canvas_w > 20000 or canvas_h > 10000:
        logger.error("Sequential stitch: canvas too large (%dx%d)", canvas_w, canvas_h)
        return None

    panorama = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)

    # Warp each image onto the canvas (reverse order so earlier images are on top)
    for i in reversed(range(len(images))):
        H_final = shift @ cumulative[i]
        warped = cv2.warpPerspective(images[i], H_final, (canvas_w, canvas_h))
        # Only fill where panorama is still black
        mask = (panorama.max(axis=2) == 0)
        panorama[mask] = warped[mask]

    return panorama


def stitch_images(image_paths: list[Path], output_path: Path) -> bool:
    """
    Stitch ordered images into a panorama.
    Pipeline: enhance → OpenCV global stitcher → sequential pairwise fallback.
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

    # Enhance contrast to help feature detection on low-texture surfaces
    images = _enhance_images(raw_images)

    # Try 1: OpenCV global stitcher (best quality when it works)
    panorama = _try_opencv_stitcher(images)

    # Try 2: sequential pairwise fallback (handles repeating patterns / camera issues)
    if panorama is None:
        logger.info("Falling back to sequential pairwise stitching...")
        panorama = _sequential_stitch(images)

    if panorama is None:
        logger.error("All stitching methods failed")
        return False

    panorama = _crop_black_borders(panorama)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), panorama, [cv2.IMWRITE_JPEG_QUALITY, 95])
    logger.info("Panorama saved to %s (%dx%d)", output_path, panorama.shape[1], panorama.shape[0])
    return True

import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def stitch_images(image_paths: list[Path], output_path: Path) -> bool:
    """
    Stitch ordered images into a panorama using OpenCV.
    Returns True on success, False on failure.
    """
    images = []
    for path in image_paths:
        img = cv2.imread(str(path))
        if img is None:
            logger.warning("Failed to read image: %s", path)
            continue
        images.append(img)

    if len(images) < 2:
        logger.error("Need at least 2 images to stitch, got %d", len(images))
        return False

    logger.info("Stitching %d images...", len(images))
    stitcher = cv2.Stitcher.create(cv2.Stitcher_PANORAMA)
    status, panorama = stitcher.stitch(images)

    if status != cv2.Stitcher_OK:
        status_names = {
            cv2.Stitcher_ERR_NEED_MORE_IMGS: "NEED_MORE_IMGS",
            cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL: "HOMOGRAPHY_EST_FAIL",
            cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL: "CAMERA_PARAMS_ADJUST_FAIL",
        }
        logger.error("Stitching failed with status: %s", status_names.get(status, str(status)))
        return False

    # Crop black borders from the stitched result
    gray = cv2.cvtColor(panorama, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
        panorama = panorama[y : y + h, x : x + w]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), panorama, [cv2.IMWRITE_JPEG_QUALITY, 95])
    logger.info("Panorama saved to %s (%dx%d)", output_path, panorama.shape[1], panorama.shape[0])
    return True

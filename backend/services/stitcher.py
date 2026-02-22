from pathlib import Path

import cv2


def stitch_images(image_paths: list[Path], output_path: Path) -> bool:
    """
    Stitch ordered images into a panorama.
    Returns True on success, False on failure.
    """
    images = []
    for path in sorted(image_paths):
        img = cv2.imread(str(path))
        if img is None:
            continue
        images.append(img)

    if len(images) < 2:
        return False

    stitcher = cv2.Stitcher.create(cv2.Stitcher_PANORAMA)
    status, panorama = stitcher.stitch(images)

    if status != cv2.Stitcher_OK:
        return False

    gray = cv2.cvtColor(panorama, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
        panorama = panorama[y : y + h, x : x + w]

    cv2.imwrite(str(output_path), panorama, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return True

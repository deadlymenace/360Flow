# 360Flow — Project Notes

## Architecture

- **Backend**: Python/FastAPI server in `backend/`
- **Mobile**: React Native (Expo) app in `mobile/`
- **iOS**: Native SwiftUI app in `ios/`
- Backend serves panorama stitching via OpenCV; mobile/iOS apps capture and display

## Known Pitfalls

### React Native / Expo
- **Do NOT use `blob.arrayBuffer()`** — it doesn't exist in React Native's runtime. Use `expo-file-system` (`downloadAsync` / `ExpoFile.downloadFileAsync`) for binary downloads instead.
- **Do NOT use `fetch().then(r => r.blob())` for file downloads** — prefer Expo FileSystem APIs which write directly to disk and return a local URI.
- **Use actual image dimensions** (`Image.getSize()`) instead of hardcoded sizes when rendering user-generated images. Hardcoded sizes cause unnecessary quality loss.

### Backend / Networking
- **Bind to `0.0.0.0`**, not `127.0.0.1`, when the backend needs to be reachable from physical devices on the same network.
- **Use the machine's LAN IP** in the mobile app's API base URL for physical device testing (not `localhost`).

### Equirectangular stitch (`backend/services/stitcher.py`)
- Project each photo as a pinhole camera at its captured yaw onto one shared equirectangular canvas, feather-blend the overlaps, and fill the poles so the JPEG is exactly 2:1. See `stitch_images`.
- **Do NOT use `cv2.Stitcher` as the primary path.** Bundle adjustment fails on a full loop (`CAMERA_PARAMS_ADJUST_FAIL`), and a planar stitch is not a 2:1 equirectangular image.
- **Do NOT chain 2D homographies.** Each homography assumes a planar scene; chaining them through a 360° rotation accumulates error exponentially (canvases have exploded to 172363×196330 px).
- **Do NOT fall back to the old cylindrical strip** (`_cylindrical_stitch` is gone). A cylinder-then-crop produced the wrong aspect. The current warp is equirectangular from the start. Optional ORB refinement may run on a cylinder only to estimate a yaw offset, and only when every neighbor matches and the loop still closes near 360°. Otherwise the captured yaws are kept.
- **Downscale inputs to ~1500px on the longest side** (`MAX_INPUT_DIM`) before projection. Full-resolution iPhone photos make feature matching ambiguous and inflate the canvas.
- Default horizontal FOV is **92°** (`DEFAULT_HFOV_DEG` / `ULTRA_WIDE_PORTRAIT_HFOV`): portrait ultra-wide on iPhone 11+, about 120° on the diagonal. The main wide portrait camera is about 53°. The app sends `hfov_deg` and `yaws_deg`. If `hfov_deg` is omitted, EXIF 35mm-equivalent focal length is used, then 92°.
- Output width is `clamp(round(2π * focal), 640, 4096)`, made even. Height is exactly `width / 2`. JPEG quality 95.
- Longitude 0, latitude 0 is the equirect center. Yaw positive looks toward +longitude. `render_perspective` is the inverse and is what the synthetic test uses.

### Staging disclosure
- The original panorama key is never overwritten. Staged bytes live under `staged/{id}.jpg`.
- Every staged view (app, share page, 3D page) and every exported staged JPEG shows **Virtually staged (AI)** (`DISCLOSURE_TEXT`). The sphere texture is the clean JPEG so the burned bar is not mapped onto the south pole; the HUD chip still shows the words.
- `build_staging_prompt` puts a fixed preservation instruction first (walls, windows, floors, fixtures) and appends the user's text only as style and furniture. This is best effort: the model can still alter the room. Keep the original so the result can be compared.

## Conventions
- Backend uses `uvicorn` with `--host 0.0.0.0`
- Mobile app uses Expo. `EXPO_PUBLIC_API_URL` is the backend origin for an installed build
- Original panoramas are JPEG, quality 95, stored at `panoramas/{id}.jpg`
- Capture uses 16 segments at 22.5° each

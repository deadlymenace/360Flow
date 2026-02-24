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

### OpenCV Stitcher
- **Set `setPanoConfidenceThresh(0.3)`** — the default (1.0) is too strict and drops edge segments, resulting in incomplete 360° panoramas.
- When stitching a full 360° loop, try appending a duplicate of the first image to help close the gap between the last and first segments.

## Conventions
- Backend uses `uvicorn` with `--host 0.0.0.0`
- Mobile app uses Expo Dev Client
- Panorama images are JPEG, quality 95, stored in `storage/panoramas/`
- Capture uses 16 segments at 22.5° each

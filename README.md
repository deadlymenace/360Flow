# 360Flow

A mobile app for real estate professionals that captures 360° panoramas from phone photos, applies AI virtual staging, and generates navigable 3D worlds.

## Phase 1 — Capture + Stitch

### Backend (Python FastAPI + OpenCV)

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

API available at `http://localhost:8000` — health check at `/health`, docs at `/docs`.

### iOS App (SwiftUI)

Open `ios/ThreeSixtyFlow/` in Xcode. Requires iOS 16.0+.

- **Simulator**: UI and navigation work, but camera/motion require a physical device.
- **Physical device**: Update `APIService.baseURL` to your Mac's local IP on the same Wi-Fi network.

### Architecture

```
Phone → 16 ultra-wide photos → Upload → OpenCV stitch → 360° panorama viewer
                                          ↓ (future)
                                   NanoBanana staging → World Labs 3D generation
```

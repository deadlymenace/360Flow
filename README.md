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

### Mobile App (Expo / React Native)

Works on **any OS** — no Mac required.

#### Quick Start

```bash
cd mobile
npm install
npx expo start
```

Scan the QR code with **Expo Go** (iOS App Store / Google Play) to run on your phone.

#### Testing on a Physical Device

1. Make sure your phone and computer are on the same Wi-Fi network
2. Update `BASE_URL` in `mobile/src/services/api.ts` to your computer's local IP:
   ```
   const BASE_URL = 'http://192.168.1.100:8000/api/v1';
   ```
3. Start the backend, then `npx expo start`

#### Building for Production (No Mac Needed)

```bash
npm install -g eas-cli
eas login
eas build --platform ios     # Cloud build, no Mac required
eas build --platform android  # Android APK/AAB
```

### Legacy iOS App (SwiftUI)

The original native iOS app is in `ios/ThreeSixtyFlow/`. Requires Xcode on macOS.

### Architecture

```
Phone → 16 ultra-wide photos → Upload → OpenCV stitch → 360° panorama viewer
                                          ↓ (future)
                                   NanoBanana staging → World Labs 3D generation
```

### Mobile App Structure

```
mobile/
├── App.tsx                    # Entry point
├── src/
│   ├── navigation.tsx         # Stack navigator (Home → Capture → Review → Processing → Panorama)
│   ├── models/types.ts        # TypeScript types, constants, helpers
│   ├── services/api.ts        # HTTP client matching all backend endpoints
│   ├── utils/haptics.ts       # Haptic feedback wrapper
│   ├── components/
│   │   └── RotationGuide.tsx   # SVG rotation ring overlay
│   └── screens/
│       ├── HomeScreen.tsx      # Project list + create new capture
│       ├── CaptureScreen.tsx   # Camera + gyroscope guided 360° capture
│       ├── ReviewScreen.tsx    # 4×4 thumbnail grid, retake, upload
│       ├── ProcessingScreen.tsx # Upload progress + stitch polling
│       └── PanoramaScreen.tsx  # Pan-to-explore panorama viewer
```

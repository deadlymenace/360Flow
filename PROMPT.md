# 360Flow — Phase 1 Implementation Prompt

> Copy-paste this entire prompt into **Codex** or **Claude Code CLI** to build Phase 1.

---

## Project Overview

Build **Phase 1 of 360Flow** — an iOS app for real estate professionals that captures 16 overlapping ultra-wide photos as the user rotates in a room, uploads them to a Python backend, stitches them into a single 360° equirectangular panorama using OpenCV, and displays the result in an interactive panorama viewer.

**Phase 1 scope only. Do not implement auth, payments, or team features.**

The end-to-end flow:
```
User opens app → starts new capture → guided camera rotates 360°
→ captures 16 photos → reviews thumbnails → uploads to backend
→ backend stitches with OpenCV → user views 360° panorama in-app
```

Future phases (AI virtual staging via NanoBanana, 3D world generation via World Labs) are **stubbed with mock endpoints** — do not call any real external APIs.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| iOS App | SwiftUI, AVFoundation, CoreMotion, SceneKit |
| Backend | Python 3.11+, FastAPI, OpenCV |
| Storage | Local filesystem (mock S3) |
| External APIs | Mock stubs only (NanoBanana, World Labs) |
| Auth | None (Phase 1) |
| Min iOS | 16.0 |

---

## Directory Structure

Create this exact structure inside the existing repo at the current working directory. The repo already has `README.md` and `PLAN.md` — do not modify those.

```
360Flow/
├── ios/
│   └── ThreeSixtyFlow/
│       ├── ThreeSixtyFlow.xcodeproj/
│       └── ThreeSixtyFlow/
│           ├── App/
│           │   ├── ThreeSixtyFlowApp.swift
│           │   └── ContentView.swift
│           ├── Views/
│           │   ├── HomeView.swift
│           │   ├── CaptureView.swift
│           │   ├── CaptureReviewView.swift
│           │   ├── ProcessingView.swift
│           │   └── PanoramaView.swift
│           ├── ViewModels/
│           │   ├── CaptureViewModel.swift
│           │   └── ProjectViewModel.swift
│           ├── Models/
│           │   ├── Project.swift
│           │   └── CaptureSegment.swift
│           ├── Services/
│           │   ├── CameraService.swift
│           │   ├── MotionService.swift
│           │   └── APIService.swift
│           ├── Utilities/
│           │   └── HapticManager.swift
│           ├── Info.plist
│           └── Assets.xcassets/
├── backend/
│   ├── requirements.txt
│   ├── main.py
│   ├── routers/
│   │   ├── __init__.py
│   │   └── projects.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── stitcher.py
│   │   ├── staging_mock.py
│   │   └── world_gen_mock.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── project.py
│   └── storage/
│       ├── __init__.py
│       └── local.py
├── PLAN.md          ← already exists, do not modify
├── PROMPT.md        ← this file, do not modify
└── README.md        ← already exists, do not modify
```

---

## Backend Specification

### requirements.txt

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
python-multipart==0.0.17
opencv-python-headless==4.10.0.84
Pillow==11.0.0
```

### main.py — FastAPI Entry Point

- Create FastAPI app with title "360Flow API", version "0.1.0"
- Include the projects router with prefix `/api/v1`
- Add CORS middleware allowing all origins (development only)
- Create the upload storage directory on startup: `./storage/uploads/`
- Health check at `GET /health` returning `{"status": "ok"}`

### models/project.py — Pydantic Models

```python
class ProjectStatus(str, Enum):
    CREATED = "created"
    UPLOADING = "uploading"
    STITCHING = "stitching"
    STITCHED = "stitched"
    STAGING = "staging"       # mock
    STAGED = "staged"         # mock
    GENERATING_3D = "generating_3d"  # mock
    COMPLETE = "complete"     # mock
    FAILED = "failed"

class Project(BaseModel):
    id: str                   # UUID
    name: str
    status: ProjectStatus
    created_at: datetime
    image_count: int = 0
    panorama_path: Optional[str] = None
    error: Optional[str] = None
```

Use an **in-memory dict** for project storage (no database in Phase 1).

### routers/projects.py — API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/projects` | Create new project. Body: `{"name": "Living Room"}`. Returns Project. |
| `GET` | `/projects` | List all projects. Returns `List[Project]`. |
| `GET` | `/projects/{id}` | Get single project with current status. |
| `POST` | `/projects/{id}/upload` | Upload 16 images as multipart form data. Field name: `images` (list of UploadFile). Saves to `./storage/uploads/{project_id}/`. Triggers stitching in background thread. Returns `{"status": "stitching"}`. |
| `GET` | `/projects/{id}/status` | Poll current status. Returns `{"status": "...", "progress": 0-100}`. |
| `GET` | `/projects/{id}/panorama` | Returns the stitched panorama as a JPEG FileResponse. 404 if not ready. |
| `POST` | `/projects/{id}/stage` | **(MOCK)** Accepts `{"prompt": "modern living room"}`. Starts mock staging. |
| `GET` | `/projects/{id}/stage/status` | **(MOCK)** Returns staged status after 3 seconds. |
| `POST` | `/projects/{id}/generate-3d` | **(MOCK)** Starts mock 3D generation. |
| `GET` | `/projects/{id}/generate-3d/status` | **(MOCK)** Returns complete after 5 seconds with fake asset URLs. |

### services/stitcher.py — OpenCV Stitching

```python
import cv2
import numpy as np
from pathlib import Path

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

    # Crop black borders
    gray = cv2.cvtColor(panorama, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
        panorama = panorama[y:y+h, x:x+w]

    cv2.imwrite(str(output_path), panorama, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return True
```

Run this in a **background thread** via `threading.Thread` (no Celery needed for Phase 1). Update the project status as it progresses.

### services/staging_mock.py — NanoBanana Mock

```python
import asyncio

async def mock_stage(panorama_path: str, prompt: str) -> dict:
    """Simulate NanoBanana staging API. Returns after 3s delay."""
    await asyncio.sleep(3)
    return {
        "status": "staged",
        "staged_image_path": panorama_path,  # just return the original
        "prompt_used": prompt,
        "provider": "nanobanana_mock"
    }
```

### services/world_gen_mock.py — World Labs Mock

```python
import asyncio

async def mock_generate_3d(panorama_path: str) -> dict:
    """Simulate World Labs 3D generation. Returns after 5s delay."""
    await asyncio.sleep(5)
    return {
        "status": "complete",
        "splat_url": "https://mock.worldlabs.ai/assets/fake-splat.spz",
        "mesh_url": "https://mock.worldlabs.ai/assets/fake-mesh.glb",
        "thumbnail_url": "https://mock.worldlabs.ai/assets/fake-thumb.jpg",
        "provider": "worldlabs_mock"
    }
```

### storage/local.py — Local Filesystem Storage

- `save_upload(project_id: str, filename: str, data: bytes) -> Path` — saves to `./storage/uploads/{project_id}/{filename}`
- `get_panorama_path(project_id: str) -> Path` — returns `./storage/panoramas/{project_id}.jpg`
- `ensure_dirs()` — creates `storage/uploads/` and `storage/panoramas/` directories

---

## iOS App Specification

### Info.plist — Required Permissions

Add these usage descriptions:
- `NSCameraUsageDescription`: "360Flow needs camera access to capture panoramic photos of rooms."
- `NSMotionUsageDescription`: "360Flow uses motion sensors to guide you through a 360° rotation."

### App/ThreeSixtyFlowApp.swift

Standard SwiftUI app entry point. No special setup needed.

### App/ContentView.swift

NavigationStack-based root view. Routes:
- **HomeView** — default screen, shows project list + "New Capture" button
- **CaptureView** — guided camera capture
- **CaptureReviewView** — review 16 photos before upload
- **ProcessingView** — upload + stitching progress
- **PanoramaView** — interactive 360° viewer

### Services/CameraService.swift

An `NSObject` class conforming to `ObservableObject` that wraps AVFoundation.

Key implementation:

```swift
import AVFoundation
import UIKit

class CameraService: NSObject, ObservableObject {
    let session = AVCaptureSession()
    private let photoOutput = AVCapturePhotoOutput()
    private var currentDevice: AVCaptureDevice?

    func configure() {
        session.beginConfiguration()
        session.sessionPreset = .photo

        // Prefer ultra-wide, fall back to wide
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.builtInUltraWideCamera, .builtInWideAngleCamera],
            mediaType: .video,
            position: .back
        )
        guard let device = discovery.devices.first,
              let input = try? AVCaptureDeviceInput(device: device) else { return }

        if session.canAddInput(input) { session.addInput(input) }
        if session.canAddOutput(photoOutput) { session.addOutput(photoOutput) }

        currentDevice = device
        session.commitConfiguration()
    }

    func capturePhoto(delegate: AVCapturePhotoCaptureDelegate) {
        let settings = AVCapturePhotoSettings()
        settings.flashMode = .off
        photoOutput.capturePhoto(with: settings, delegate: delegate)
    }

    func start() { DispatchQueue.global(qos: .userInitiated).async { self.session.startRunning() } }
    func stop() { session.stopRunning() }
}
```

To show the camera preview in SwiftUI, create a `UIViewRepresentable` wrapping `AVCaptureVideoPreviewLayer`.

### Services/MotionService.swift

An `ObservableObject` wrapping CoreMotion for yaw tracking.

```swift
import CoreMotion
import Combine

class MotionService: ObservableObject {
    private let motionManager = CMMotionManager()
    private let queue = OperationQueue()

    @Published var currentYaw: Double = 0  // degrees, -180 to 180
    @Published var currentSegmentIndex: Int = 0

    static let segmentCount = 16
    static let segmentAngle = 360.0 / Double(segmentCount)  // 22.5°

    func startTracking() {
        guard motionManager.isDeviceMotionAvailable else { return }
        motionManager.deviceMotionUpdateInterval = 1.0 / 30.0  // 30 Hz

        motionManager.startDeviceMotionUpdates(
            using: .xArbitraryZVertical,
            to: queue
        ) { [weak self] motion, error in
            guard let motion = motion, let self = self else { return }
            let yawDeg = motion.attitude.yaw * 180.0 / .pi
            DispatchQueue.main.async {
                self.currentYaw = yawDeg
                // Normalize to 0-360 range, then find segment
                let normalized = (yawDeg + 360).truncatingRemainder(dividingBy: 360)
                self.currentSegmentIndex = Int(normalized / Self.segmentAngle) % Self.segmentCount
            }
        }
    }

    func stopTracking() {
        motionManager.stopDeviceMotionUpdates()
    }
}
```

### ViewModels/CaptureViewModel.swift

Orchestrates the capture flow:

- Holds an array of 16 `CaptureSegment` slots (initially nil)
- Owns `CameraService` and `MotionService`
- Tracks which segments have been captured via a `Set<Int>`
- **Auto-capture logic**: when `currentSegmentIndex` changes to an uncaptured segment AND the yaw is within ±5° of the segment center, trigger capture automatically (or highlight the capture button)
- On capture: store the `UIImage` + yaw angle in the corresponding segment slot
- Trigger haptic feedback on each successful capture via `HapticManager`
- Provide computed property `capturedCount: Int` and `isComplete: Bool` (all 16 captured)
- Allow retaking: clear a segment and mark it uncaptured

### Models/CaptureSegment.swift

```swift
struct CaptureSegment: Identifiable {
    let id: Int          // 0-15, the segment index
    let targetYaw: Double  // center angle in degrees for this segment
    var image: UIImage?
    var actualYaw: Double?
    var capturedAt: Date?

    var isCaptured: Bool { image != nil }
}
```

### Models/Project.swift

```swift
struct Project: Codable, Identifiable {
    let id: String
    var name: String
    var status: String
    var createdAt: Date
    var imageCount: Int
    var panoramaAvailable: Bool
}
```

### Views/CaptureView.swift

The primary capture screen:

1. **Full-screen camera preview** (via UIViewRepresentable wrapping AVCaptureVideoPreviewLayer)
2. **Rotation guide overlay** — a circular ring around the edges showing 16 segments:
   - Each segment is an arc (22.5°)
   - **Green fill** = captured
   - **Gray fill** = not yet captured
   - **Pulsing highlight** = current segment (where device is pointing)
   - A small arrow/indicator showing current yaw direction
3. **Capture count** — "4 / 16" text display
4. **Manual capture button** — large circular button at bottom center. Tapping captures current segment.
5. When all 16 captured → show "Review" button to navigate to CaptureReviewView

Draw the rotation ring using SwiftUI `Canvas` or `Path` with arcs. The ring should rotate as the device rotates, keeping segment positions aligned with physical directions.

### Views/CaptureReviewView.swift

- **4x4 grid** of thumbnail images (each segment's photo)
- Tap a thumbnail to retake that segment (navigates back to CaptureView targeting that segment)
- **"Upload & Stitch" button** at the bottom, enabled when all 16 are captured
- Tapping begins the upload flow → navigates to ProcessingView

### Views/ProcessingView.swift

Two phases shown sequentially:
1. **Uploading** — progress bar showing upload completion (0-100%)
2. **Stitching** — indeterminate spinner with "Stitching your panorama..." text, polling `GET /projects/{id}/status` every 2 seconds

On completion → navigate to PanoramaView. On failure → show error with retry button.

### Views/PanoramaView.swift

Interactive 360° panorama viewer using SceneKit:

```swift
import SceneKit
import SwiftUI

struct PanoramaView: View {
    let panoramaImage: UIImage

    var body: some View {
        SceneView(scene: makePanoramaScene(), options: [.allowsCameraControl])
            .ignoresSafeArea()
    }

    private func makePanoramaScene() -> SCNScene {
        let scene = SCNScene()

        // Sphere with panorama texture mapped on the inside
        let sphere = SCNSphere(radius: 10)
        sphere.segmentCount = 96
        let material = SCNMaterial()
        material.diffuse.contents = panoramaImage
        material.isDoubleSided = true
        sphere.firstMaterial = material

        let sphereNode = SCNNode(geometry: sphere)
        // Flip normals inward so texture renders on inside
        sphereNode.scale = SCNVector3(x: -1, y: 1, z: 1)
        scene.rootNode.addChildNode(sphereNode)

        // Camera at center of sphere
        let cameraNode = SCNNode()
        cameraNode.camera = SCNCamera()
        cameraNode.camera?.fieldOfView = 70
        cameraNode.position = SCNVector3(x: 0, y: 0, z: 0)
        scene.rootNode.addChildNode(cameraNode)

        return scene
    }
}
```

The `.allowsCameraControl` option gives free pan/zoom with gestures. The user can drag to look around inside the panorama and pinch to zoom.

### Views/HomeView.swift

Simple project list screen:
- List of existing projects with name, status badge, and thumbnail
- "New Capture" button (prominent, top or floating)
- Tap a completed project → navigate to its PanoramaView
- Empty state: "No captures yet. Tap + to start."

### Services/APIService.swift

HTTP client for the backend:

```swift
class APIService {
    static let shared = APIService()
    private let baseURL: String

    init(baseURL: String = "http://localhost:8000/api/v1") {
        self.baseURL = baseURL
    }

    // Create project
    func createProject(name: String) async throws -> Project { ... }

    // Upload 16 images as multipart form data
    func uploadImages(projectId: String, images: [UIImage],
                      onProgress: @escaping (Double) -> Void) async throws { ... }

    // Poll status
    func getProjectStatus(projectId: String) async throws -> ProjectStatusResponse { ... }

    // Download panorama image
    func getPanorama(projectId: String) async throws -> UIImage { ... }
}
```

For the multipart upload, construct the `multipart/form-data` body manually or use `URLSession` upload task. Each image should be JPEG-compressed at quality 0.8. The field name for each file should be `images`.

### Utilities/HapticManager.swift

```swift
import UIKit

struct HapticManager {
    static func capture() {
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
    }
    static func success() {
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }
    static func error() {
        UINotificationFeedbackGenerator().notificationOccurred(.error)
    }
}
```

---

## Build Order

Implement in this exact order. Complete and test each step before moving to the next.

1. **Create backend directory structure** — all folders, `__init__.py` files, `requirements.txt`
2. **`backend/main.py`** — FastAPI app with CORS, health check, storage directory creation on startup
3. **`backend/models/project.py`** — Pydantic models and in-memory store
4. **`backend/storage/local.py`** — Local filesystem save/retrieve functions
5. **`backend/services/stitcher.py`** — OpenCV stitching function (standalone, testable)
6. **`backend/routers/projects.py`** — All endpoints: create, upload, status, panorama, mock stubs
7. **`backend/services/staging_mock.py`** — NanoBanana mock (3s delay)
8. **`backend/services/world_gen_mock.py`** — World Labs mock (5s delay)
9. **Create iOS project structure** — Xcode project with SwiftUI, all folders and empty files
10. **`CameraService.swift`** — AVFoundation setup with ultra-wide preference
11. **`MotionService.swift`** — CoreMotion yaw tracking at 30Hz
12. **`HapticManager.swift`** — Haptic feedback utility
13. **`CaptureSegment.swift` + `Project.swift`** — Data models
14. **`CaptureViewModel.swift`** — Capture orchestration logic
15. **`CaptureView.swift`** — Camera preview + rotation guide overlay
16. **`CaptureReviewView.swift`** — 4x4 thumbnail grid + retake + upload button
17. **`APIService.swift`** — HTTP client with multipart upload
18. **`ProcessingView.swift`** — Upload progress + stitching status polling
19. **`PanoramaView.swift`** — SceneKit 360° viewer
20. **`HomeView.swift`** — Project list + new capture button
21. **`ContentView.swift` + `ThreeSixtyFlowApp.swift`** — Navigation wiring
22. **End-to-end test** — Verify full flow works

---

## Verification

### Backend Testing

```bash
# Install dependencies
cd backend && pip install -r requirements.txt

# Start server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Test health check
curl http://localhost:8000/health
# Expected: {"status":"ok"}

# Create a project
curl -X POST http://localhost:8000/api/v1/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Room"}'
# Expected: {"id":"<uuid>","name":"Test Room","status":"created",...}

# Upload images (use any test JPEGs)
curl -X POST http://localhost:8000/api/v1/projects/<id>/upload \
  -F "images=@test1.jpg" -F "images=@test2.jpg" -F "images=@test3.jpg"
# Expected: {"status":"stitching"}

# Poll status
curl http://localhost:8000/api/v1/projects/<id>/status
# Expected: {"status":"stitching","progress":50} → eventually {"status":"stitched","progress":100}

# Download panorama
curl http://localhost:8000/api/v1/projects/<id>/panorama --output panorama.jpg

# Test mock staging
curl -X POST http://localhost:8000/api/v1/projects/<id>/stage \
  -H "Content-Type: application/json" \
  -d '{"prompt": "modern living room"}'

# Test mock 3D generation
curl -X POST http://localhost:8000/api/v1/projects/<id>/generate-3d
```

### iOS Testing

- **Simulator**: Camera and motion won't work. Test the UI layout, navigation flow, and API calls using mock/placeholder images.
- **Physical device**: Test the full capture flow. Verify:
  - Ultra-wide camera activates (wider field of view than normal)
  - Rotation guide segments highlight correctly as you rotate
  - Haptic feedback fires on each capture
  - All 16 photos appear in review grid
  - Upload sends images to backend
  - Stitching status polls correctly
  - Panorama loads in SceneKit viewer with pan/zoom

### End-to-End

1. Start backend on Mac: `uvicorn main:app --host 0.0.0.0 --port 8000`
2. Update `APIService.baseURL` to your Mac's local IP (e.g., `http://192.168.1.100:8000/api/v1`)
3. Run iOS app on physical device on same Wi-Fi network
4. Capture 16 photos → upload → wait for stitching → view panorama

---

## Important Notes

- **No database** — use in-memory dict. Data resets on server restart. This is intentional for Phase 1.
- **No auth** — all endpoints are public. This is intentional for Phase 1.
- **JPEG compression** — compress photos to quality 0.8 on iOS before uploading. Each image should be ~1-2MB.
- **Background stitching** — use `threading.Thread` in Python. Do NOT block the API request.
- **Error handling** — if stitching fails (OpenCV returns non-OK status), set project status to "failed" with an error message. Show the error in ProcessingView with a retry option.
- **The rotation guide ring** is the key UX differentiator. Spend time making it feel smooth and intuitive. Use `withAnimation` for segment state changes.
- **Do not import any packages not listed in requirements.txt** for the backend.
- **Do not use any third-party Swift packages** — only Apple frameworks (SwiftUI, AVFoundation, CoreMotion, SceneKit, UIKit).

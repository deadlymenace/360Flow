# 360Flow - Mobile App Plan

## Vision

A mobile app for real estate professionals that captures a room with multiple photos, stitches them into a 360° panorama, applies AI virtual staging, and generates a navigable 3D world — all from a phone.

---

## Core Workflow (User Perspective)

1. **Capture** - Agent stands in center of room, app guides them to take 16 overlapping ultra-wide photos in a full rotation
2. **Stitch** - Photos are uploaded and stitched into a single equirectangular 360° panorama
3. **Stage** - AI virtually stages the panorama (add furniture, decor) based on a text prompt (e.g., "modern minimalist living room")
4. **Generate 3D** - The staged panorama is sent to World Labs API to produce a navigable 3D world
5. **Share** - Agent gets a shareable link or embeddable viewer for listings

---

## Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Mobile App** | React Native + Expo | Cross-platform (iOS/Android), large ecosystem, Expo simplifies camera access and builds |
| **Camera** | expo-camera | Native camera access with manual controls (focus, exposure, zoom) |
| **Backend API** | Python (FastAPI) | Best OpenCV support for stitching, async-friendly, clean API design |
| **Image Stitching** | OpenCV (server-side) | Mature panorama stitching module; no viable on-device RN solution exists |
| **Virtual Staging** | NanoBanana API | ~$0.02/image, natural language prompts, photorealistic output |
| **3D Generation** | World Labs API (Marble) | Accepts panoramic images (`is_pano: true`), outputs navigable 3D splats + mesh |
| **3D Viewer** | WebView + Three.js / Gaussian Splat Viewer | Render splats in-app or via shareable web link |
| **Storage** | AWS S3 or Cloudflare R2 | Store captured photos, panoramas, staged images, 3D assets |
| **Database** | PostgreSQL | Projects, users, job status tracking |
| **Auth** | Clerk or Firebase Auth | Quick setup, social login support |
| **Hosting** | Railway / Fly.io (backend), Vercel (web viewer) | Simple deployment, scales with demand |

---

## Architecture

```
┌─────────────────────────────────────┐
│         React Native App            │
│  ┌───────────┐  ┌────────────────┐  │
│  │  Camera    │  │  Gallery /     │  │
│  │  Capture   │  │  Project Mgmt  │  │
│  │  (16 imgs) │  │                │  │
│  └─────┬─────┘  └───────┬────────┘  │
│        │                │           │
│  ┌─────▼────────────────▼────────┐  │
│  │     3D Viewer (WebView)       │  │
│  │     Gaussian Splat Renderer   │  │
│  └───────────────────────────────┘  │
└──────────────┬──────────────────────┘
               │ REST API
┌──────────────▼──────────────────────┐
│         FastAPI Backend             │
│                                     │
│  ┌──────────┐  ┌─────────────────┐  │
│  │ /capture │  │ /projects       │  │
│  │ /stitch  │  │ /auth           │  │
│  │ /stage   │  │ /share          │  │
│  │ /generate│  │                 │  │
│  └────┬─────┘  └─────────────────┘  │
│       │                             │
│  ┌────▼──────────────────────────┐  │
│  │  Pipeline Orchestrator        │  │
│  │  (Celery / ARQ task queue)    │  │
│  │                               │  │
│  │  Step 1: Receive 16 images    │  │
│  │  Step 2: OpenCV stitch → pano │  │
│  │  Step 3: NanoBanana stage     │  │
│  │  Step 4: World Labs 3D gen    │  │
│  │  Step 5: Store assets → S3    │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

---

## Phase Breakdown

### Phase 1: Foundation (MVP Camera + Stitching)

**Goal:** Capture photos on mobile, stitch into a panorama on the server.

1. **Expo project setup**
   - Initialize Expo managed workflow with TypeScript
   - Configure navigation (Expo Router)
   - Set up project structure: screens, components, services, hooks

2. **Guided camera capture screen**
   - Full-screen camera view with overlay guide
   - Visual rotation indicator (pie/compass showing 16 segments)
   - Haptic feedback on each capture
   - Auto-detect device ultra-wide lens and prefer it
   - Store captured images locally before upload
   - Allow re-taking individual segments

3. **Image upload service**
   - Compress images client-side (quality 0.8 JPEG, ~1-2MB each)
   - Upload 16 images to backend with progress indicator
   - Retry logic for failed uploads

4. **FastAPI backend scaffold**
   - Project structure: routes, services, models, tasks
   - Image upload endpoint (multipart)
   - PostgreSQL models: User, Project, PanoramaJob
   - S3 integration for image storage

5. **OpenCV stitching pipeline**
   - Receive 16 images, order by capture sequence
   - Use `cv2.Stitcher` in `PANORAMA` mode
   - Post-process: crop black borders, normalize to equirectangular projection
   - Output: single equirectangular JPEG/PNG (high-res)
   - Task queue (Celery/ARQ) for async processing
   - WebSocket or polling for status updates to mobile app

**Deliverable:** User captures 16 photos → sees stitched 360° panorama in-app.

---

### Phase 2: AI Virtual Staging

**Goal:** Let users apply AI staging to their panorama with a text prompt.

1. **Staging prompt UI**
   - Panorama preview with interactive viewer (pan/zoom)
   - Text input for staging prompt (e.g., "luxury modern living room with grey sofa")
   - Preset style templates (Modern, Scandinavian, Industrial, Traditional, etc.)
   - Before/after comparison slider

2. **NanoBanana API integration**
   - Backend service to call NanoBanana API
   - Send panorama + staging prompt
   - Handle response (staged image)
   - Store original + staged versions
   - Allow re-staging with different prompts

3. **Staging result review**
   - Side-by-side or overlay comparison
   - Accept / re-stage workflow
   - Save multiple staging variations per panorama

**Deliverable:** User writes a staging prompt → sees AI-furnished panorama.

---

### Phase 3: 3D World Generation

**Goal:** Convert staged panorama into a navigable 3D environment.

1. **World Labs API integration**
   - Backend service to call World Labs generation endpoint
   - Submit staged panorama with `is_pano: true` flag
   - Poll for completion (~5 min generation time)
   - Download output assets: Gaussian splats (SPZ), GLB mesh, panorama, thumbnail

2. **3D viewer**
   - WebView-based Gaussian splat renderer in the mobile app
   - Build a lightweight web viewer page (Three.js + splat loader)
   - Touch controls: pan, zoom, navigate
   - Loading state with progress indication
   - Host viewer on Vercel for shareable links

3. **Asset management**
   - Store all 3D assets (splats, mesh) in S3
   - Generate shareable URLs with optional expiration
   - Embed code generator for listing websites

**Deliverable:** User gets a navigable 3D world from their staged panorama, shareable via link.

---

### Phase 4: Project Management + Polish

**Goal:** Full workflow for managing multiple property scans.

1. **Project dashboard**
   - List of properties/rooms scanned
   - Status indicators (capturing → stitching → staging → generating → ready)
   - Thumbnail previews
   - Search and filter

2. **User auth + accounts**
   - Sign up / login (email, Google, Apple)
   - Profile management
   - Usage tracking / credits system

3. **Sharing + export**
   - Shareable web links for 3D tours
   - Embed snippets for MLS/listing sites
   - Export panorama as image file
   - Download 3D assets (splat, mesh) for use in other tools

4. **Notifications**
   - Push notifications when pipeline stages complete
   - Email notifications for completed 3D worlds

---

### Phase 5: Advanced Features (Post-MVP)

- **Multi-room tours:** Link multiple 360° captures into a walkthrough
- **Floor plan generation:** Estimate room dimensions from 3D mesh
- **Batch processing:** Scan entire property (multiple rooms) in one session
- **Team features:** Brokerage accounts, shared projects, agent assignments
- **White-label sharing:** Custom branding on shared 3D tour links
- **Offline mode:** Queue captures for processing when back online
- **Analytics:** Track views on shared 3D tours

---

## API Cost Estimates (Per Room)

| Step | Service | Estimated Cost |
|------|---------|---------------|
| Stitching | Self-hosted OpenCV | ~$0.00 (compute only) |
| Virtual Staging | NanoBanana API | ~$0.02 - $0.12 |
| 3D Generation | World Labs API (Marble 0.1-mini) | TBD (credits-based) |
| Storage | S3/R2 | ~$0.01 |
| **Total per room** | | **< $1.00 estimated** |

---

## Key Technical Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Poor stitching from handheld capture | Broken panoramas | Guided overlay UI with gyroscope-assisted alignment; reject poor-quality captures before upload |
| NanoBanana output quality on panoramas | Staging artifacts | Test with equirectangular inputs early; fall back to segmented staging (stage regions then re-stitch) |
| World Labs API latency (~5 min) | User wait time | Async pipeline with push notifications; show intermediate results (staged pano) while 3D generates |
| World Labs API cost uncertainty | Budget risk | Start with Marble 0.1-mini for dev; track credit usage; implement user credit system |
| Large file uploads on mobile data | Failed uploads | Chunked uploads with resume; compress aggressively; Wi-Fi-only option |

---

## Recommended Starting Point

**Start with Phase 1** — get the camera capture and stitching working end-to-end. This is the foundation everything else builds on, and it validates the hardest technical piece (producing quality panoramas from phone photos).

### Immediate First Steps

1. `npx create-expo-app@latest 360Flow --template blank-typescript`
2. Set up the guided camera capture screen with rotation overlay
3. Scaffold the FastAPI backend with an upload + stitch endpoint
4. Wire them together and test the full capture → stitch loop

---

## External API References

- **World Labs API Docs:** https://docs.worldlabs.ai/api
- **World Labs Platform:** https://platform.worldlabs.ai
- **NanoBanana API:** https://nanobananaapi.ai/
- **Expo Camera:** https://docs.expo.dev/versions/latest/sdk/camera/
- **OpenCV Stitcher:** https://docs.opencv.org/4.x/d2/d8d/classcv_1_1Stitcher.html

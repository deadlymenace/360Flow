# 360Flow

A mobile app for real estate professionals that captures 360° panoramas from phone photos, applies AI virtual staging, and generates navigable 3D worlds.

The working app is the Expo project in `mobile/`. The API is the FastAPI service in `backend/`. The SwiftUI app in `ios/` is the earlier native prototype and is not part of this pipeline.

## Flow

1. On an iPhone 11 or newer, the app captures 16 portrait ultra-wide photos at 22.5° steps and records each shot's yaw and horizontal field of view.
2. The API stitches those photos into a 2:1 equirectangular JPEG and keeps that original.
3. The app and the public share page show the panorama in a 360° viewer.
4. A text prompt or a furniture preset asks NanoBanana to add furniture and decor. The original stays stored. Every staged view and every exported staged JPEG carries the label **Virtually staged (AI)** and can be compared with the original.
5. World Labs Marble turns the staged panorama into a navigable 3D world (`is_pano`). The app and the share page open that world when Marble returns a link.

## Run it locally

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Health check: `http://localhost:8000/health`. Interactive docs: `http://localhost:8000/docs`.

With no database URL the API uses SQLite. With no R2 credentials it writes files under `STORAGE_DIR` (default `./storage`, which is `backend/storage` when you start uvicorn from `backend/`). In Docker that directory is `/data`.

### Tests

```bash
cd backend
python -m pytest -q
```

The stitch tests build a synthetic overlapping capture and check that the panorama is exactly 2:1.

### Mobile

```bash
cd mobile
npm install
cp .env.example .env
npx expo start
```

`EXPO_PUBLIC_API_URL` is the backend origin, with or without `/api/v1`. For a phone on the same Wi-Fi, set it to `http://<your-lan-ip>:8000`. When that variable is empty, Expo Go falls back to the debugger host on port 8000, then `localhost`.

If the backend has `API_KEY` set, put the same value in `EXPO_PUBLIC_API_KEY`. The app sends it as `X-API-Key`. Share pages under `/v/` stay public.

Ultra-wide capture needs a physical iPhone. The simulator will not list that lens; the app then keeps the 92° portrait ultra-wide default until a wide-only camera is reported, in which case it uses 53°.

Typecheck:

```bash
cd mobile
npx tsc --noEmit
```

### Installed builds

`mobile/eas.json` is set up for EAS Build. Set `EXPO_PUBLIC_API_URL` and, when the API is locked, `EXPO_PUBLIC_API_KEY` in the EAS environment for the build profile. Do not commit those values.

```bash
cd mobile
npm install -g eas-cli
eas login
eas build --platform ios
```

An iOS build that you can install through TestFlight needs an Apple Developer account.

## Mock providers and real APIs

`GET /health` reports which path is active:

| Field | Empty key | Key set |
| --- | --- | --- |
| `staging` | `mock` | `nanobanana` |
| `world` | `mock` | `worldlabs` |
| `storage` | `local` | `r2` |
| `database` | `sqlite` | `postgres` |
| `auth` | `open` | `api-key` |

Project records use `nanobanana_mock` / `nanobanana` and `worldlabs_mock` / `worldlabs`.

Leave `NANOBANANA_API_KEY` unset and staging applies a local color grade. It does not add furniture. Set the key and the API calls [NanoBanana 2](https://nanobananaapi.ai/) (`POST /api/v1/nanobanana/generate-2`, then poll `record-info`). NanoBanana has to download the panorama, so the image URL must be a presigned R2 URL or `PUBLIC_BASE_URL` on a public host. A key with neither of those fails the job and names `PUBLIC_BASE_URL` or a presigned URL in the error. It does not silently fall back to the mock.

Leave `WORLDLABS_API_KEY` unset and world generation returns mock asset URLs and no Marble link. The viewer then shows the staged room on a sphere and says a navigable world has not been generated yet. Set the key and the API uploads the clean staged JPEG and calls [Marble](https://docs.worldlabs.ai/api) `worlds:generate` with `is_pano: true` and `WORLDLABS_MODEL` (default `marble-1.1`). Worlds are public when `WORLDLABS_PUBLIC` is true, so the Marble link can be shared.

## Staging label and preservation

The unstaged panorama is stored at `panoramas/{id}.jpg` and is never overwritten by staging. The clean staged JPEG is stored at `staged/{id}.jpg`.

- In the app, Original / Compare / Staged switches the 360 viewer. Staged and Compare show **Virtually staged (AI)**.
- The public page `/v/{share_token}` has the same modes and the same label. `/v/{share_token}/world` keeps the label when the source was staged.
- `GET /v/{token}/staged.jpg` and the in-app export download that JPEG with the label burned into a bar along the bottom.
- The sphere texture (`/v/{token}/texture.jpg`) is the clean staged JPEG so the bar is not smeared across the south pole. The on-screen label is still shown.

The user's prompt only chooses style and furniture. `build_staging_prompt` in `backend/services/disclosure.py` always puts a fixed instruction first: preserve walls, windows, doors, glass, floors, ceilings, and fixtures, and only add movable furniture and decor. Presets in the app describe furniture only.

This is best effort. NanoBanana can still change architecture even when the prompt forbids it. The original panorama stays available so a person can compare and discard a bad result. The product does not claim the model guarantees preservation.

## Deploy on Railway

The service root is the repository root. Railway should build the root `Dockerfile` (`railway.toml` sets `dockerfilePath` and the `/health` check).

1. Create a Railway project from this repo and confirm the service uses that Dockerfile.
2. Add a Postgres plugin and set `DATABASE_URL` to its connection string. `postgres://` URLs are accepted.
3. Set `PUBLIC_BASE_URL` to the public Railway URL, with no trailing slash. Share links and NanoBanana both use it.
4. Set `API_KEY` if the API should require `X-API-Key`. Share pages stay open.
5. Set `NANOBANANA_API_KEY` and `WORLDLABS_API_KEY` when those accounts exist. Until then the health check reports `mock` for that step.
6. Optional: mount a volume at `/data` when you are still on local disk. `STORAGE_DIR` is already `/data` in the image.

## Cloudflare R2

1. Create an R2 bucket and an API token that can read and write objects in it.
2. Set these on the Railway service (or in `backend/.env` locally):

```
S3_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com
S3_BUCKET=<bucket>
S3_ACCESS_KEY_ID=<access key>
S3_SECRET_ACCESS_KEY=<secret>
S3_REGION=auto
```

All three of bucket, access key, and secret must be set. The API then stores uploads, the original panorama, and the staged image in R2 and presigns the original for NanoBanana. Leave them empty and files stay on local disk.

## Environment variables

Backend (`backend/.env.example`):

| Variable | Purpose |
| --- | --- |
| `PUBLIC_BASE_URL` | Public origin for share links and NanoBanana image fetch |
| `API_KEY` | Shared secret for `/api/v1`. Blank leaves the API open |
| `DATABASE_URL` | Postgres URL. Blank uses SQLite |
| `STORAGE_DIR` | Local files. Docker uses `/data` |
| `S3_ENDPOINT_URL` | R2 S3 endpoint |
| `S3_BUCKET` | R2 bucket |
| `S3_ACCESS_KEY_ID` | R2 access key |
| `S3_SECRET_ACCESS_KEY` | R2 secret |
| `S3_REGION` | `auto` for R2 |
| `CORS_ORIGINS` | Comma-separated origins, or `*` |
| `NANOBANANA_API_KEY` | Blank uses the staging mock |
| `NANOBANANA_RESOLUTION` | `1K`, `2K`, or `4K` (default `2K`) |
| `WORLDLABS_API_KEY` | Blank uses the world mock |
| `WORLDLABS_MODEL` | Default `marble-1.1` |
| `WORLDLABS_PUBLIC` | Default `true` so the Marble link is shareable |
| `STITCH_CONCURRENCY` | Stitch and generation jobs at once |
| `JOBS_INLINE` | Await jobs inside the request. Tests only |
| `MOCK_STAGE_DELAY` | Seconds the mock staging waits |
| `MOCK_WORLD_DELAY` | Seconds the mock world job waits |
| `MAX_IMAGE_BYTES` | Per-image upload cap (default 12 MB) |
| `MIN_IMAGES` / `MAX_IMAGES` | Upload count bounds (2–24). The app still sends 16 |
| `LOG_LEVEL` | Uvicorn/app log level |

Mobile (`mobile/.env.example`):

| Variable | Purpose |
| --- | --- |
| `EXPO_PUBLIC_API_URL` | Backend origin for dev and installed builds |
| `EXPO_PUBLIC_API_KEY` | Sent as `X-API-Key` when the API is locked |

## What you need to supply

- A [NanoBanana](https://nanobananaapi.ai/) API key, if staging should add furniture instead of the color-grade mock.
- A [World Labs](https://docs.worldlabs.ai/api) API key (and credits), if the 3D step should return a real Marble world.
- A Railway account for the API, and a public URL to put in `PUBLIC_BASE_URL`.
- A Cloudflare R2 bucket and token for photo storage. Local disk works for a single server with a `/data` volume.
- An Apple Developer account to ship the iPhone app through TestFlight. Ultra-wide capture is iPhone 11 and newer.

## Layout

```
backend/          FastAPI API, stitcher, staging, World Labs, share viewer
mobile/           Expo app: capture, 360 viewer, staging, 3D, share
ios/              Legacy SwiftUI prototype
Dockerfile        Railway image (context is the repo root)
railway.toml      Dockerfile builder and /health check
```

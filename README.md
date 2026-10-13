# 360Flow

A mobile app for real estate professionals that captures 360° panoramas from phone photos, generates a navigable 3D walkthrough, and can optionally add AI virtual staging.

The working app is the Expo project in `mobile/`. The API is the FastAPI service in `backend/`. The SwiftUI app in `ios/` is the earlier native prototype and is not part of this pipeline.

## Flow

1. On an iPhone 11 or newer, the app captures 16 portrait ultra-wide photos at 22.5° steps and records each shot's yaw and horizontal field of view.
2. The API stitches those photos into an exact 2:1 equirectangular JPEG. The left and right edges are sealed so they match. A 16-shot ring covers about ±54° of latitude, so the zenith and nadir are inpainted caps, not photographed ceiling and floor. Optional upward and downward shots can be added later if the ceiling and floor need real geometry.
3. The app and the public share page show the panorama immediately.
4. World Labs Marble builds a 3D walkthrough from that original panorama (`is_pano`). A fast draft (`marble-1.0-draft`, about 20 seconds) is shown while the full world (`marble-1.1`) generates. Full worlds have taken about 10 minutes, and the client keeps polling for at least 15.
5. Staging is optional. A prompt or furniture preset asks Gemini to add furniture and decor on five overlapping views, keeping the original ceiling and floor. The unstaged panorama stays stored. Every staged view, every staged 3D world, and every exported staged JPEG carries the label **Virtually staged (AI)** and can be compared with the original. The owner sees that comparison privately first. The public share page keeps showing only the original panorama and original 3D until the owner taps **Publish staged view**. Unpublish hides the staged view again. A staged panorama can get its own 3D world.

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

The app registers an anonymous device and keeps that token in the device secure store. It sends `Authorization: Bearer` on API calls. Do not put `API_KEY` in the app. `API_KEY` is an operator key sent as `X-API-Key` and is compared with `hmac.compare_digest`. Share pages under `/v/` stay public until the share token is rotated or the capture is deleted.

Ultra-wide capture needs a physical iPhone. The simulator will not list that lens; the app then keeps the 92° portrait ultra-wide default until a wide-only camera is reported, in which case it uses 53°.

Typecheck:

```bash
cd mobile
npx tsc --noEmit
```

### Installed builds

`mobile/eas.json` is set up for EAS Build. Set `EXPO_PUBLIC_API_URL` in the EAS environment for the build profile. Do not commit that value, and do not set `API_KEY` in the app environment. If a build already shipped with `EXPO_PUBLIC_API_KEY`, rotate `API_KEY` after deploy.

```bash
cd mobile
npm install -g eas-cli
eas login
eas build --platform ios
```

An iOS build that you can install through TestFlight needs an Apple Developer account.

## Mock providers and real APIs

`GET /health` returns `{"status": "ok"}`. The same request with the operator `X-API-Key` reports which path is active:

| Field | Empty key | Key set |
| --- | --- | --- |
| `staging` | `mock` | `gemini` |
| `world` | `mock` | `worldlabs` |
| `storage` | `local` | `r2` |
| `database` | `sqlite` | `postgres` |
| `auth` | `open` | `api-key` |

Project records use `gemini_mock` / `gemini` and `worldlabs_mock` / `worldlabs`.

Leave `GEMINI_API_KEY` unset and staging applies a local color grade. It does not add furniture. Set the key and the API calls the [Gemini image API](https://ai.google.dev/gemini-api/docs/image-generation): `POST https://generativelanguage.googleapis.com/v1/models/{model}:generateContent` with header `x-goog-api-key`, inline JPEG bytes, and `responseModalities` of `TEXT` and `IMAGE`. The default model is `gemini-3.1-flash-image`. Override with `GEMINI_MODEL` if you need a different id. A provider quota or `429 RESOURCE_EXHAUSTED` response tells the client `AI staging is unavailable right now (provider quota)`. Any other Gemini failure says `Staging failed. Try again.` and leaves the panorama and 3D world in place. Staging status is stored separately from the project status. It does not silently fall back to the mock.

Leave `WORLDLABS_API_KEY` unset and world generation returns mock asset URLs and no Marble link. The viewer then shows the panorama on a sphere and says a navigable world has not been generated yet. Set the key and the API uploads the clean panorama (the original, or the unlabeled staged JPEG for a staged world) and calls [Marble](https://docs.worldlabs.ai/api) `worlds:generate` with `is_pano: true`. The draft model `marble-1.0-draft` runs first. Its world id and Marble URL are stored while `marble-1.1` (`WORLDLABS_MODEL`) continues, and the app shows that draft. A draft failure is logged and the full model still runs. The full-world poll waits 20 minutes. Set `WORLDLABS_DRAFT_MODEL` to `off` to skip the draft. Worlds are private unless `WORLDLABS_PUBLIC` is true. The name sent to Marble is the project id. Deleting a capture deletes the draft world, the full world, and the same pair for a staged world. A world that finishes after the capture was deleted is deleted too.

## Staging label and preservation

The unstaged panorama is stored at `panoramas/{id}.jpg` and is never overwritten by staging. The clean staged JPEG is stored at `staged/{id}.jpg`.

- In the app, Original / Compare / Staged switches the 360 viewer. Staged and Compare show **Virtually staged (AI)**. That viewer uses a private preview URL returned only on the owner's project. The Share button still sends the public link.
- The public page `/v/{share_token}` shows the original panorama and the original 3D walkthrough. Staged controls, `staged.jpg`, `texture.jpg`, and `/world?source=staged` stay off that page until the owner publishes. `/v/{share_token}/world` is the original walkthrough and hides the label. After publish, `/v/{share_token}/world?source=staged` keeps the label.
- After publish, `GET /v/{token}/staged.jpg` downloads the JPEG with the label burned into a bar along the bottom. Before publish, that URL is 404. The in-app export uses the private preview URL, so the owner can save the labeled JPEG first.
- After publish, the sphere texture (`/v/{token}/texture.jpg`) is the clean staged JPEG so the bar is not smeared across the south pole. The on-screen label is still shown. The owner preview uses the same clean texture.

The user's prompt only chooses style and furniture. `build_staging_prompt` in `backend/services/disclosure.py` is the fixed instruction: preserve walls, windows, doors, glass, floors, ceilings, and fixtures, and only add movable furniture and decor. The style text is a separate quoted content part, length-capped, and is not concatenated into those rules. Presets in the app describe furniture only. A view where Gemini answers with text and no image is retried once; a second text-only answer fails that staging run.

This is best effort. Gemini can still change architecture even when the prompt forbids it. Each view is checked against the original edges; a view that moves walls or windows is retried once and then kept as the original photo, with a note on the project. The original panorama stays available so a person can compare and discard a bad result. The product does not claim the model guarantees preservation.

## Ceiling, floor, and the seam

Marble expects an exact 2:1 equirectangular image whose left and right edges match. The stitcher writes that aspect and spreads any left/right difference across a short band so the edges meet.

A ring of 16 portrait ultra-wide photos leaves the zenith and nadir unphotographed (about ±54° of latitude is covered). Those caps are filled with a pole-centered inpaint so the poles are not black. The fill is a smooth continuation of the nearest photographed rows. It is not a picture of the ceiling or the floor. Adding optional shots aimed up and down would capture that geometry; the stitcher can already place a photo by pitch, and the capture flow does not take those shots yet.

## Deploy on Railway

The service root is the repository root. The image is the root `Dockerfile`. `.railway/railway.ts` records the `/health` check for Railway's config-as-code. Railway does not read that file during a normal deploy. After `railway` is linked, review and apply it:

```bash
railway config plan
railway config apply
```

`railway.toml` is removed because Railway stops honoring it on 2026-12-01 and it blocks the new config file. Review the plan before apply. If the service name in the plan does not match the service already in the project, applying can delete resources. Restart policy is set in the dashboard; the current DSL does not include it.

1. Create a Railway project from this repo and confirm the service builds the root Dockerfile.
2. Add a Postgres plugin and set `DATABASE_URL` to its connection string. `postgres://` URLs are accepted.
3. Set `PUBLIC_BASE_URL` to the public Railway URL, with no trailing slash. Share links use it.
4. Set `API_KEY` only for operators. The iPhone app uses a per-device token instead. Share pages stay open to anyone with the current share token.
5. Set `GEMINI_API_KEY` and `WORLDLABS_API_KEY` when those accounts exist. Until then the health check reports `mock` for that step.
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

All three of bucket, access key, and secret must be set. The API then stores uploads, the original panorama, and the staged image in R2. Leave them empty and files stay on local disk. Gemini receives image bytes directly, so staging does not need a public image URL.

## Environment variables

Backend (`backend/.env.example`):

| Variable | Purpose |
| --- | --- |
| `PUBLIC_BASE_URL` | Public origin for share links |
| `API_KEY` | Operator key (`X-API-Key`). Not for the app. Blank means device tokens only |
| `DATABASE_URL` | Postgres URL. Blank uses SQLite |
| `STORAGE_DIR` | Local files. Docker uses `/data` |
| `S3_ENDPOINT_URL` | R2 S3 endpoint |
| `S3_BUCKET` | R2 bucket |
| `S3_ACCESS_KEY_ID` | R2 access key |
| `S3_SECRET_ACCESS_KEY` | R2 secret |
| `S3_REGION` | `auto` for R2 |
| `CORS_ORIGINS` | Comma-separated browser origins. Empty allows none |
| `GEMINI_API_KEY` | Blank uses the staging mock |
| `GEMINI_MODEL` | Default `gemini-3.1-flash-image` |
| `GEMINI_IMAGE_SIZE` | `1K`, `2K`, or `4K` (default `1K`) |
| `GEMINI_JOBS_PER_DEVICE_PER_DAY` | Paid staging cap. Default 20. `0` disables it |
| `WORLDLABS_JOBS_PER_DEVICE_PER_DAY` | Paid world cap, including the automatic world. Default 10. `0` disables it |
| `GLOBAL_DAILY_WORLDS_CAP` | Paid worlds for every device combined, per UTC day. Default 100. `0` disables it |
| `WORLD_DELAY_MAX_HOURS` | Fail a credit-delayed 3D tour after this many hours. Default 24 |
| `WORLD_DELAY_MAX_ATTEMPTS` | Fail a credit-delayed 3D tour after this many attempts. Default 48 |
| `WORLDLABS_API_KEY` | Blank uses the world mock |
| `WORLDLABS_DRAFT_MODEL` | Default `marble-1.0-draft`. `off` skips the fast preview |
| `WORLDLABS_MODEL` | Full world. Default `marble-1.1` |
| `WORLDLABS_LOW_CREDIT_THRESHOLD` | Admin health warns below this balance. Default 6000 |
| `WORLDLABS_PUBLIC` | Default `false`. Worlds are private |
| `WORKER_CONCURRENCY` | Stitch, staging, and world jobs at once. Default 3 |
| `STITCH_CONCURRENCY` | CPU threads for panorama stitching. Default 1 |
| `JOBS_INLINE` | Await jobs inside the request. Tests only |
| `MOCK_STAGE_DELAY` | Seconds the mock staging waits |
| `MOCK_WORLD_DELAY` | Seconds the mock world job waits |
| `MAX_IMAGE_BYTES` | Per-image upload cap (default 12 MB) |
| `MIN_IMAGES` / `MAX_IMAGES` | Upload count bounds (2–24). The app still sends 16 |
| `LOG_LEVEL` | Uvicorn/app log level |
| `TRUSTED_PROXY_COUNT` | X-Forwarded-For hops to trust. Default 1 (Railway) |
| `SHARE_LINK_TTL_DAYS` | Share link lifetime. Default 30. `0` does not expire |
| `EMBED_FRAME_ANCESTORS` | Parents allowed when `embed=1`. Empty keeps `frame-ancestors 'none'` |

Mobile (`mobile/.env.example`):

| Variable | Purpose |
| --- | --- |
| `EXPO_PUBLIC_API_URL` | Backend origin for dev and installed builds |

## Privacy

The app does not create user accounts, so there is no account to delete. Each capture is the stored data: photos, the panorama, a staged image, and the share link.

Before the first upload, the app shows a consent screen. Agreeing lets the app send room photos to the 360Flow API. The API stitches them and sends the panorama to World Labs to build the 3D walkthrough. Virtual staging is optional; choosing it sends the panorama to Google Gemini. Until the person agrees, the upload does not start.

`DELETE /api/v1/projects/{id}` removes that capture for the device that owns it. Another device gets 404. It deletes the database row, the uploaded photos, the panorama, the staged image, and any files stored for that capture on local disk or R2. When `WORLDLABS_API_KEY` is set, it also asks World Labs to delete the full world, the draft world, and the staged pair. The share link then returns 404. `POST /api/v1/projects/{id}/share/rotate` issues a new share token and retires the old one. Rotating the share link does not change the owner's private preview. A staged image is not on the public page until `POST /api/v1/projects/{id}/staged/publish`. `POST /api/v1/projects/{id}/staged/unpublish` hides it again. Starting a new stage, or uploading a new capture, clears that approval. Another device gets 404 for both. In the app, Delete on the home screen and on the panorama screen asks for confirmation before calling it.

`/docs` and `/openapi.json` stay off unless `ENABLE_DOCS=true`. Share pages send `X-Content-Type-Options`, `Referrer-Policy`, `Strict-Transport-Security`, `X-Robots-Tag: noindex`, and a Content-Security-Policy. `frame-ancestors` is `'none'` unless the URL has `embed=1` and `EMBED_FRAME_ANCESTORS` lists parents. An empty list keeps `'none'`. Share links are built from `PUBLIC_BASE_URL` only and expire after `SHARE_LINK_TTL_DAYS` (default 30). Rotating a link starts a new window. Invalid request bodies return `422` with `Invalid request` and do not echo the submitted values.

Paid Gemini and World Labs calls are capped per device per day (`GEMINI_JOBS_PER_DEVICE_PER_DAY`, `WORLDLABS_JOBS_PER_DEVICE_PER_DAY`). `GLOBAL_DAILY_WORLDS_CAP` limits paid worlds across every device. The count is an atomic increment, and it is taken when the job is queued. The world that starts automatically after upload counts against both caps. A project has one open job. Replacing a capture queues the old Marble worlds for deletion before their ids are cleared. A tour that is waiting on credits can be retried or cancelled, and it fails after `WORLD_DELAY_MAX_HOURS` or `WORLD_DELAY_MAX_ATTEMPTS`. Cancelling it before World Labs has accepted a generation refunds that daily quota unit. `X-API-Key` attempts on `/health` share one database rate limit across replicas. The public `/health` body is only `{"status": "ok"}`. A Marble delete that fails is stored and retried in the background. Client IP for rate limits comes from the rightmost trusted `X-Forwarded-For` hop (`TRUSTED_PROXY_COUNT`, default 1 for Railway). Failed authentication is limited by that IP, including `X-API-Key` attempts on `/health`.

## What you need to supply

- A [Gemini](https://ai.google.dev/gemini-api/docs/image-generation) API key (`GEMINI_API_KEY`), if staging should add furniture instead of the color-grade mock.
- A [World Labs](https://docs.worldlabs.ai/api) API key (`WORLDLABS_API_KEY`) and credits, if the 3D step should return a real Marble world. The draft model runs first, then the full model.
- A Railway account for the API, and a public URL to put in `PUBLIC_BASE_URL`.
- A Cloudflare R2 bucket and token for photo storage. Local disk works for a single server with a `/data` volume.
- An Apple Developer account to ship the iPhone app through TestFlight. Ultra-wide capture is iPhone 11 and newer.

## Layout

```
backend/          FastAPI API, stitcher, staging, World Labs, share viewer
mobile/           Expo app: capture, 360 viewer, staging, 3D, share
ios/              Legacy SwiftUI prototype
Dockerfile        Railway image (context is the repo root)
.railway/railway.ts  Health check for `railway config apply`
```

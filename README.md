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

`EXPO_PUBLIC_API_URL` is the backend origin, with or without `/api/v1`. For a phone on the same Wi-Fi during development, set it to `http://<your-lan-ip>:8000`. When that variable is empty, a dev build falls back to the debugger host on port 8000, then `localhost`. A release build (`__DEV__` is false) fails unless the variable is set to an `https://` URL. The `expo-build-properties` plugin sets Android `usesCleartextTraffic` to false. iOS still allows local networking so a dev phone can reach the API on the LAN.

The app registers an anonymous device and keeps that token in the device secure store. It sends `Authorization: Bearer` on API calls. Do not put `API_KEY` in the app. `API_KEY` is an operator key sent as `X-API-Key` and is compared with `hmac.compare_digest`. Share pages under `/v/` stay public until the share token is rotated or the capture is deleted.

Ultra-wide capture needs a physical iPhone. The simulator will not list that lens; the app then keeps the 92° portrait ultra-wide default until a wide-only camera is reported, in which case it uses 53°.

Typecheck:

```bash
cd mobile
npx tsc --noEmit
```

### Installed builds

`mobile/eas.json` is set up for EAS Build. The preview and production profiles set `EXPO_PUBLIC_API_URL` to `https://backend-production-adb6.up.railway.app`. That host is the staging API, the only backend for now, and both profiles should move to a production URL when one exists. Those release builds require that https URL. Do not set `API_KEY` in the app environment. If a build already shipped with `EXPO_PUBLIC_API_KEY`, rotate `API_KEY` after deploy. The camera plugin sets `microphonePermission` and `recordAudioAndroid` to false. iOS keeps `NSAllowsLocalNetworking` so a dev phone can reach the API on the LAN.

```bash
cd mobile
npm install -g eas-cli
eas login
eas build --platform ios
```

An iOS build that you can install through TestFlight needs an Apple Developer account.

### Over-the-air updates

`expo-updates` publishes a JavaScript bundle to a channel on an existing build. `runtimeVersion` uses the app version policy, so the runtime version is `expo.version` (currently `1.1.0`, the Expo SDK 57 app). An SDK 54 build on `1.0.0` will not receive this JavaScript. Bump the version again when native code changes, then ship a new build before publishing an update for it.

`mobile/eas.json` sets a channel on each build profile: `development`, `preview`, and `production`.

Update manifests are signed. The public certificate is `mobile/certs/certificate.pem` (`keyid` `main`, algorithm `rsa-v1_5-sha256`). The private key stays outside the repo. Do not commit `*.key`, `private-key.pem`, or a `keys/` directory.

The EAS project is `deadlymenace`, id `cc2c18fc-3a1a-4554-82bb-be2545db1b0c`. `updates.url` is `https://u.expo.dev/cc2c18fc-3a1a-4554-82bb-be2545db1b0c`.

Publish a signed update. Pass the private key from outside the repo, and use the channel name that matches the build:

```bash
cd mobile
eas update --channel <name> --private-key-path <path outside repo>
```

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

Leave `WORLDLABS_API_KEY` unset and world generation returns mock asset URLs and no Marble link. The viewer then shows the panorama on a sphere and says a navigable world has not been generated yet. Set the key and the API uploads the clean panorama (the original, or the unlabeled staged JPEG for a staged world) and calls [Marble](https://docs.worldlabs.ai/api) `worlds:generate` with `is_pano: true`. The draft model `marble-1.0-draft` runs first. Its world id and Marble URL are stored while `marble-1.1` (`WORLDLABS_MODEL`) continues, and the app shows that draft. A draft failure other than a rate limit or out-of-credits response is logged and the full model still runs. HTTP 429 is retried with exponential backoff and jitter, never sooner than a `Retry-After` header, and only up to `WORLDLABS_RETRY_ATTEMPTS` (at most 8). A job also stops after `WORLDLABS_RATE_LIMIT_MAX_ATTEMPTS` or `WORLDLABS_RATE_LIMIT_MAX_SECONDS` of consecutive HTTP 429s. A successful poll or generate resets that window. The project is then failed only when a run of 429s spends the budget. Once a draft operation id exists, a later attempt polls that operation instead of calling `worlds:generate` again. Each running job holds a lease (`JOB_LEASE_SECONDS`) that a heartbeat renews while it waits for a worker slot, calls Gemini, or sleeps on a 429. A renewal that raises is retried while the lease is still valid. If the lease is lost, the job stops before the next World Labs or Gemini call. Another worker claims the job only after that lease expires, and then polls the saved operation instead of generating again. `lease_expires_at` and `rate_limit_started_at` are `timestamptz`, and database sessions use UTC. `WORLDLABS_CONCURRENCY` (default 2) caps how many World Labs jobs run at once, separately from `WORKER_CONCURRENCY`. The full-world poll waits 20 minutes. Set `WORLDLABS_DRAFT_MODEL` to `off` to skip the draft. Worlds are private unless `WORLDLABS_PUBLIC` is true. The name sent to Marble is the project id. Deleting a capture, or regenerating a world, deletes the previous full world and the previous draft world at World Labs. Each deletion is logged with its world id. A world that finishes after the capture was deleted is deleted too.

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
| `GLOBAL_DAILY_CREDITS_CAP` | World Labs credits for every device combined, per UTC day. Default 30000 (about 18 worlds at 150 draft + 1500 full). `0` disables it |
| `SHARE_RATE_PER_MINUTE` | Requests per IP for public share pages (`/v/`) and owner previews (`/p/`). Default 60 |
| `WORLD_DELAY_MAX_HOURS` | Fail a credit-delayed 3D tour after this many hours. Default 24 |
| `WORLD_DELAY_MAX_ATTEMPTS` | Fail a credit-delayed 3D tour after this many attempts. Default 48 |
| `WORLDLABS_API_KEY` | Blank uses the world mock |
| `WORLDLABS_DRAFT_MODEL` | Default `marble-1.0-draft`. `off` skips the fast preview |
| `WORLDLABS_MODEL` | Full world. Default `marble-1.1` |
| `WORLDLABS_LOW_CREDIT_THRESHOLD` | Admin health warns below this balance. Default 6000 |
| `WORLDLABS_PUBLIC` | Default `false`. Worlds are private |
| `WORLDLABS_CONCURRENCY` | World Labs jobs at once. Default 2. Separate from `WORKER_CONCURRENCY` |
| `WORLDLABS_RETRY_ATTEMPTS` | HTTP 429 attempts per World Labs request. Default 5, maximum 8 |
| `WORLDLABS_RETRY_BASE_SECONDS` | First backoff delay before jitter. Default 0.5, maximum 60 |
| `WORLDLABS_RETRY_MAX_SECONDS` | Cap on a 429 wait, including Retry-After. Default 32, maximum 60 |
| `WORLDLABS_RATE_LIMIT_MAX_ATTEMPTS` | Total 429s for one 3D job. Default 20, maximum 40 |
| `WORLDLABS_RATE_LIMIT_MAX_SECONDS` | Total time spent rate-limited on one 3D job. Default 900, maximum 3600 |
| `JOB_LEASE_SECONDS` | How long a worker owns a running job. Default 90, maximum 600 |
| `JOB_LEASE_HEARTBEAT_SECONDS` | How often that lease is renewed. Default 30, and never more than a third of the lease |
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
| `EXPO_PUBLIC_API_URL` | Backend origin. Required and https for a release build. Preview and production EAS profiles currently use the staging Railway URL. Dev builds may use http |

## Privacy

The app does not create user accounts, so there is no account to delete. Each capture is the stored data: photos, the panorama, a staged image, and the share link.

Before the first upload, the app shows a consent screen. Agreeing lets the app send room photos to the 360Flow API. The API stitches them and sends the panorama to World Labs to build the 3D walkthrough. Virtual staging is optional; choosing it sends the panorama to Google Gemini. Until the person agrees, the upload does not start.

`DELETE /api/v1/projects/{id}` removes that capture for the device that owns it. Another device gets 404. It deletes the database row, the uploaded photos, the panorama, the staged image, and any files stored for that capture on local disk or R2. When `WORLDLABS_API_KEY` is set, it also asks World Labs to delete the full world, the draft world, and the staged pair. The share link then returns 404. `POST /api/v1/projects/{id}/share/rotate` issues a new share token and a new preview token, and retires both old ones. The preview link expires on the same `SHARE_LINK_TTL_DAYS` clock. A staged image is not on the public page until `POST /api/v1/projects/{id}/staged/publish`. `POST /api/v1/projects/{id}/staged/unpublish` hides it again. Starting a new stage, or uploading a new capture, clears that approval. Another device gets 404 for both. In the app, Delete on the home screen and on the panorama screen asks for confirmation before calling it.

**Delete all my data.** `DELETE /api/v1/devices/me` (device bearer token) first cancels the device's queued, running, and delayed jobs and drops their leases; the pipeline checks the project and lease before every write after a provider call, so a late World Labs or Gemini result writes nothing. It then deletes every project the calling device owns (never another device's projects or ownerless ones): the database rows and their jobs, the uploaded photos, panoramas, and staged images in storage (local disk or R2), and with the rows their share and preview links, which then return 404. It then revokes the device token, so later calls with it get 401. This is the in-app account deletion required by App Store guideline 5.1.1(v). When `WORLDLABS_API_KEY` is set, it also asks World Labs to delete each project's full world, draft world, and staged pair, like `DELETE /api/v1/projects/{id}`. A world that any other remaining project still references (including ownerless demo/share projects) is kept and logged, for this route and for `DELETE /api/v1/projects/{id}`, because World Labs deletion cannot be undone. That step is best-effort and runs after the data is gone: a World Labs error is logged and queued for retry, and the route still returns 204.

`/docs` and `/openapi.json` stay off unless `ENABLE_DOCS=true`. Share pages send `X-Content-Type-Options`, `Referrer-Policy`, `Strict-Transport-Security`, `X-Robots-Tag: noindex`, and a Content-Security-Policy. `frame-ancestors` is `'none'` unless the URL has `embed=1` and `EMBED_FRAME_ANCESTORS` lists parents. An empty list keeps `'none'`. Share pages do not send `X-Frame-Options: DENY`, so an allowed embed can still frame them. JSON API responses, including `/health`, send `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'`. Share links are built from `PUBLIC_BASE_URL` only and expire after `SHARE_LINK_TTL_DAYS` (default 30). Rotating a link starts a new window for the share token and the preview token. A NUL byte in a share or project URL is a 404. Public `/v/` and `/p/` pages are limited per IP by `SHARE_RATE_PER_MINUTE` (default 60). Invalid request bodies return `422` with `Invalid request` and do not echo the submitted values.

Paid Gemini and World Labs calls are capped per device per day (`GEMINI_JOBS_PER_DEVICE_PER_DAY`, `WORLDLABS_JOBS_PER_DEVICE_PER_DAY`). `GLOBAL_DAILY_WORLDS_CAP` limits how many paid worlds can start across every device. `GLOBAL_DAILY_CREDITS_CAP` (default 30000) limits the credits those worlds reserve: one queued world holds the full model (1500) plus the draft (150) when the draft model is on, about 18 worlds before the credit cap. `0` disables that credit cap. The world-count cap stays in place beside it. Both counts are atomic increments, taken when the job is queued, and a failed charge rolls back. The world that starts automatically after upload counts against them. A project has one open job. Replacing a capture or regenerating a world deletes the old full world and the old draft world at World Labs before their ids are cleared, and logs each world id. A delete that fails stays queued for retry. If a draft generation was accepted and then failed for a reason other than credits or HTTP 429, one free status GET looks for a world id and queues it for deletion. If that operation is still running, its id is stored and the same maintenance loop polls it later. That poll does not call `worlds:generate`. Upload URLs from World Labs are used only when they are https, have no credentials, and the host is `worldlabs.ai` or `storage.googleapis.com` (or a subdomain of either). The upload method must be PUT or POST. Operation ids are checked before they are placed in a poll URL. A tour that is waiting on credits can be retried or cancelled, and it fails after `WORLD_DELAY_MAX_HOURS` or `WORLD_DELAY_MAX_ATTEMPTS`. Cancelling it before World Labs has accepted a generation refunds that daily world count and the reserved credits. `X-API-Key` attempts on `/health` share one database rate limit across replicas. The public `/health` body is only `{"status": "ok"}`. A Marble delete that fails is stored and retried in the background. Client IP for rate limits comes from the rightmost trusted `X-Forwarded-For` hop (`TRUSTED_PROXY_COUNT`, default 1 for Railway). Failed authentication is limited by that IP, including `X-API-Key` attempts on `/health`. World Labs 429 retries stay clamped (at most 8 attempts, and at most 60 seconds for the base delay and for a single wait).

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

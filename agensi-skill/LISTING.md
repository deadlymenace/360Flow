# Agensi Marketplace Listing Draft — 360Flow

> Note: Per Agensi's submission rules, buyer-facing listing copy below contains **no external links** to GitHub, personal websites, or outside checkouts. Sample output URLs use `api.example.com`.

## Title (max 60 chars)

`360Flow — 360° Room Panoramas & AI Staging (3D Beta)` *(52 chars)*

## Summary (max 300 chars)

`Turn 2 to 24 overlapping room photos into a sealed 360° panorama and labeled AI-staged view. Default runs panorama-only; optional World Labs Atlas 3D walkthroughs (--3d) are an untested beta. Bring your own Gemini and (if using --3d) Atlas API keys.` *(247 chars)*

## Categories (pick up to 3; first is primary)

1. Real Estate / Design / 3D & Media *(pick exact matching slugs from Agensi's categories at submit time)*
2. Productivity / Operations
3. Creative / Imaging

## Description (Markdown)

Turn a sequence of room photos into a shareable 360° panorama, an optional AI-staged room preview, and an opt-in **untested beta** 3D property walkthrough right from your AI agent.

### What it does
1. **Validates your environment first:** Checks your 360Flow backend URL (`FLOW360_API_URL`), Google Gemini API key (`GEMINI_API_KEY` when `--stage` is used), and World Labs Atlas API key (`ATLAS_API_KEY` only when `--3d` is passed) before making any network call.
2. **Core (Default) — Stitches a seamless 2:1 equirectangular panorama:** Accepts 2 to 24 JPEG or PNG photos (16 portrait ultra-wide shots at ~22.5° steps recommended), aligns them with optional yaw, pitch, and FOV metadata, inpaints zenith and nadir caps, and seals the 360° seam—with zero 3D API charges by default.
3. **Core — Optional AI virtual staging with disclosure:** Stages empty rooms using Google Gemini while checking structural edges against the original walls, windows, and floors. Staged results remain private for your review until published and always carry a `Virtually staged (AI)` label.
4. **Opt-In Untested Beta — 3D walkthroughs (`--3d`, World Labs Atlas):** When `--3d` is explicitly passed, slices the stitched panorama into overlapping pinhole crops, submits them to World Labs Atlas v2 (draft mode by default), and hosts the resulting Gaussian splat in a self-hosted 3D viewer link. Labeled **untested beta**—not yet verified in a live Atlas run on sliced panoramas.
5. **Share-link lifecycle controls:** Rotate, immediately revoke, or permanently delete captures and their hosted views anytime.

### What you get back
- Public 360° panorama viewer link (`/v/<token>`)
- Downloadable 2:1 equirectangular JPEG (`/v/<token>/panorama.jpg`)
- Private owner comparison link (`/p/<token>`) and labeled staged export when AI virtual staging is enabled
- Opt-in 3D walkthrough viewer link (`/v/<token>/world`) when `--3d` (untested beta) is enabled and completes

### Bring Your Own Keys (BYOK)
You supply your own API keys so compute bills go directly to your own provider accounts with zero markup:
- **360Flow backend URL** (`FLOW360_API_URL`) and optional access key (`FLOW360_API_KEY`)
- **Google Gemini API key** (`GEMINI_API_KEY`) when AI virtual staging (`--stage`) is used
- **World Labs Atlas API key** (`ATLAS_API_KEY`) only if you opt in to untested-beta 3D (`--3d`)

### Where it stops
- Does not capture photos from a phone camera directly; works on JPEG/PNG photos already exported to your workspace.
- Default runs are panorama-only (plus optional Gemini staging) with no Atlas calls; 3D requires explicit `--3d` and your own `ATLAS_API_KEY`.
- Atlas 3D walkthrough generation from single-position panorama slices is an **untested beta**—it has only been tested against mocks, and single-spot panoramas lack camera translation parallax, so live results are unverified and may fail cleanly or show depth artifacts.
- Virtual staging preservation is best-effort and verified with an edge-structure check; always inspect the private preview before publishing.

## See It in Action (700+ chars)

**Buyer prompt:**
> I captured 16 portrait photos of an empty living room (`room_01.jpg` through `room_16.jpg`). Stitch them into a 360° tour and stage it with a warm Scandinavian sofa and oak coffee table. Give me the preview and share links.

**Agent run & output:**
1. Runs preflight: `python3 scripts/flow360.py check-keys --stage` -> confirms `FLOW360_API_URL` and `GEMINI_API_KEY` are configured (no `ATLAS_API_KEY` needed for default panorama + staging).
2. Runs core panorama + staging pipeline with idempotency protection:
   `python3 scripts/flow360.py run --name "Living Room" --hfov 92 --stage "Warm Scandinavian linen sofa, oak coffee table, textured wool rug, and indoor plant" --files room_01.jpg ... room_16.jpg`
3. Returns structured result:
```json
{
  "project_id": "9f2c4a1e-8b3d-4c7a-9e12-4d8a7b6c5e10",
  "status": "stitched",
  "image_count": 16,
  "has_original_panorama": true,
  "has_staged": true,
  "staged_published": false,
  "disclosure": "Virtually staged (AI)",
  "world_ready": false,
  "share_url": "https://api.example.com/v/AbCdEfGhIjKlMnOpQrStUv",
  "panorama_download_url": "https://api.example.com/v/AbCdEfGhIjKlMnOpQrStUv/panorama.jpg",
  "owner_preview_url": "https://api.example.com/p/ZyXwVuTsRqPoNmLkJiHgFe",
  "share_expires_at": "2026-12-25T18:00:00Z"
}
```
4. Want to preview the untested-beta 3D pipeline offline before using Atlas credits? Run `python3 scripts/flow360.py dry-run` to validate the 10-crop pinhole camera geometry and see the ~$0.52 draft credit estimate with zero network calls. Then pass `--3d` (or run `generate-3d`) with `ATLAS_API_KEY` set to try `/v/<token>/world`.

## Compatibility Note

Requires Python 3.9+ (standard library only, no third-party packages), a reachable 360Flow HTTPS backend (`FLOW360_API_URL`), your Google Gemini API key (`GEMINI_API_KEY`) for AI virtual staging, and your World Labs Atlas API key (`ATLAS_API_KEY`) only when opting in to untested-beta 3D walkthroughs (`--3d`).

## Tags & Keywords

- **Tags:** `real-estate`, `360-panorama`, `virtual-staging`, `3d-tour-beta`, `property-walkthrough`, `byok`
- **Keywords:** `360 panorama`, `virtual staging`, `virtual tour`, `gaussian splat`, `real estate`, `property marketing`

## Permissions / Requires (Submit Form)

- **Requires:**
  - `360Flow API backend`
  - `Google Gemini API (generativelanguage.googleapis.com, for AI virtual staging)`
  - `World Labs Atlas API (api.atlas.worldlabs.ai, optional untested beta for --3d)`
- **Permissions:** Network access (HTTPS calls to configured `FLOW360_API_URL`), Read local files (reading 2–24 user-supplied JPEG/PNG photos for upload), Local config write (`~/.config/flow360/device.json` for device session token), Environment variables (`FLOW360_API_URL`, `GEMINI_API_KEY`, `ATLAS_API_KEY`, `FLOW360_API_KEY`).

## Limitations & FAQ

- **Why do I need my own API keys?** You pay Google Gemini and World Labs Atlas directly at their standard API rates with zero per-run markup.
- **Is 3D enabled by default?** No—default runs are panorama-only (plus optional Gemini staging) without needing `ATLAS_API_KEY`. Pass `--3d` to opt in to 3D.
- **Why is 3D walkthrough generation marked untested beta?** Atlas v2 has only been tested against mocks so far, and single-spot 360° panorama captures have no camera translation parallax. Slicing panoramas into pinhole views for Atlas is an experimental beta path (draft mode is ~$0.52/world by default).
- **What if an upload is interrupted?** Every upload sends a deterministic `Idempotency-Key` so retrying the same photo set does not trigger duplicate provider charges.

---

## Price (Agensi Submit Form)

**`$14.99` one-time** (`$10.49` net to creator after Agensi's 30% fee; buyers supply their own `GEMINI_API_KEY` and `ATLAS_API_KEY`, so creator compute cost is `$0` per buyer run).

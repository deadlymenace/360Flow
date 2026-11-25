# 360Flow Agensi Skill (BYOK Edition)

Turn 2 to 24 overlapping room photos into a sealed 2:1 360° equirectangular panorama and an optional labeled AI-staged view, with opt-in **untested beta** 3D walkthroughs powered by **World Labs Atlas** (`--3d`).

## Package Structure

```text
360flow-agensi-skill/
├── SKILL.md            # Agent instructions, network disclosure, BYOK preflight, and prompt-injection guards
├── scripts/
│   └── flow360.py      # Zero-dependency Python 3 CLI (< 20 KB)
├── .env.example        # Buyer environment variable template (placeholders only)
└── README.md           # Setup & security overview
```

## Core Features vs. Opt-In 3D (Untested Beta)

- **Core (Default & Stable):**
  - Default `run` stitches 2–24 overlapping room photos into a sealed 2:1 equirectangular 360° panorama (`/v/<token>`) with zero 3D API costs.
  - Run AI virtual staging (`--stage`) via your own `GEMINI_API_KEY`, keeping staged views private until published and always labeled `Virtually staged (AI)`.
- **Opt-In 3D Walkthroughs (World Labs Atlas — Untested Beta):**
  - 3D runs **only** when `--3d` (or `generate-3d`) is explicitly passed. The backend slices the stitched panorama into 10 overlapping `1280×720` pinhole crops (`90°` HFOV across 2 pitch rows), submits them to World Labs Atlas v2 (`images2PosedRGBD` + `posedImages2Splats`, draft mode ~$0.52/world), and hosts the resulting `.spz` splat in 360Flow's Spark.js viewer (`/v/<token>/world`).
  - **Untested beta:** Atlas v2 has only been tested against mocks so far, and single-spot panorama slices have zero camera translation parallax. Live runs on real panoramas may fail cleanly to `failed` or show flat-depth artifacts.

## Disclosed Network Services & Buyer Keys

Buyers supply their own API keys for every paid external provider the pipeline calls. No personal, creator, staging, or fallback keys are bundled, and no undisclosed servers are contacted.

| Environment Variable | Service & Hostname | Purpose | Required? |
| --- | --- | --- | --- |
| `FLOW360_API_URL` | 360Flow Backend (`https://agensi-prod-production.up.railway.app` or self-hosted) | Stitching, viewer hosting, and share links | Always |
| `GEMINI_API_KEY` | Google Gemini (`generativelanguage.googleapis.com`) | AI virtual staging (`gemini-3.1-flash-image`) | When `--stage` is used |
| `ATLAS_API_KEY` | World Labs Atlas (`api.atlas.worldlabs.ai`, header `WLT-Api-Key`) | Opt-in 3D splat generation (**untested beta**) | Only with `--3d` or `generate-3d` |
| `FLOW360_API_KEY` | 360Flow Backend | Buyer access key (if server disables open device registration) | Depends on backend policy |
| `FLOW360_DEVICE_TOKEN` | 360Flow Backend | Optional device token override (auto-saved at `~/.config/flow360/device.json`) | Optional |

## Setup Steps for a New Buyer

1. Point `FLOW360_API_URL` to the 360Flow BYOK production backend (`https://agensi-prod-production.up.railway.app`) or your own self-hosted 360Flow instance.
2. *(Optional AI virtual staging)* Obtain a **Google Gemini API key** and export `GEMINI_API_KEY`.
3. *(Optional untested-beta 3D walkthroughs)* Obtain a **World Labs Atlas API key** (`atlas.worldlabs.ai`) with `tasks.create`, `operations.read`, `assets.create`, and `assets.read` permissions, and export `ATLAS_API_KEY` only if you plan to pass `--3d`.
4. Export variables in your shell or agent runtime (never commit them to git):
   ```bash
   export FLOW360_API_URL="https://agensi-prod-production.up.railway.app"
   export GEMINI_API_KEY="REPLACE_WITH_YOUR_GEMINI_API_KEY"
   export ATLAS_API_KEY="REPLACE_WITH_YOUR_ATLAS_API_KEY"   # optional; only needed with --3d
   ```
5. Verify preflight or test Atlas payload math offline before uploading any photos:
   ```bash
   python3 scripts/flow360.py check-keys --stage
   python3 scripts/flow360.py check-keys --3d --stage
   python3 scripts/flow360.py dry-run
   ```

## Security Properties

- **Zero bundled secrets:** Contains only placeholders; safe for public marketplace packaging.
- **No hardcoded fallback:** `flow360.py` refuses to run with exit code 2 if `FLOW360_API_URL` or required provider keys are missing or left as placeholders.
- **Local device token persistence:** Auto-registered device tokens are stored under `~/.config/flow360/device.json` with `0700` directory / `0600` file permissions so follow-up `status`, `stage`, `generate-3d`, `rotate-share`, `revoke-share`, and `delete` commands reuse the same project owner identity.
- **Idempotent retries & clean failures:** Deterministic `Idempotency-Key` headers prevent duplicate charges on retries, and unexpected Atlas responses transition jobs cleanly to `failed`.
- **No environment harvesting:** Reads only `FLOW360_*`, `ATLAS_API_KEY`, and `GEMINI_API_KEY`, and redacts keys from error output.
- **Untrusted data isolation:** `SKILL.md` instructs agents to treat photo filenames, metadata, and API responses strictly as data, and `flow360.py` validates that returned share URLs match the configured `FLOW360_API_URL` origin.

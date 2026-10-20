"""Self-contained 360° and 3D share pages. No external scripts."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from html import escape

from services.disclosure import DISCLOSURE_TEXT

_PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__")

_STATIC_VIEWER = Path(__file__).resolve().parent / "static" / "viewer"


def _file_version(name: str) -> str:
    """Content hash for cache-busting our own viewer scripts."""
    return hashlib.sha256((_STATIC_VIEWER / name).read_bytes()).hexdigest()[:12]


PANO_JS_VERSION = _file_version("pano-viewer.js")
SPLAT_JS_VERSION = _file_version("splat-viewer.js")


def _config_json(values: dict) -> str:
    """Per-page viewer settings as a JSON data block (not executed, so CSP-safe).

    ``<``, ``>`` and ``&`` are escaped so a project name can never close the tag.
    """
    raw = json.dumps(values, separators=(",", ":"))
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>__TITLE__</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; height: 100%; background: #0e0e18; color: #fff;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  #view { position: fixed; inset: 0; }
  canvas { width: 100%; height: 100%; display: block; touch-action: none; }
  iframe.world { position: fixed; inset: 0; width: 100%; height: 100%; border: 0; background: #000; }
  .chip { position: absolute; top: 14px; left: 50%; transform: translateX(-50%);
    background: #1a1a2e; color: #ffe08a; border: 1px solid rgba(255, 224, 138, .7);
    padding: 8px 14px; border-radius: 999px; font-weight: 700; font-size: 14px;
    z-index: 6; pointer-events: none; white-space: nowrap; }
  .title { position: absolute; top: 16px; left: 16px; z-index: 5; font-weight: 700;
    text-shadow: 0 1px 4px #000; max-width: 46vw; }
  .bar { position: absolute; left: 0; right: 0; bottom: 0; z-index: 5; display: flex;
    gap: 8px; flex-wrap: wrap; align-items: center; justify-content: center;
    padding: 18px 12px calc(18px + env(safe-area-inset-bottom));
    background: linear-gradient(transparent, rgba(0,0,0,.78)); }
  button, a.btn { background: #221f3d; color: #fff; border: 1px solid #3d3866;
    border-radius: 999px; padding: 8px 14px; font-weight: 650; font-size: 14px;
    text-decoration: none; font-family: inherit; }
  button.active { background: #6c63ff; border-color: #6c63ff; }
  input[type=range] { width: min(180px, 40vw); accent-color: #6c63ff; }
  .note { position: absolute; left: 16px; right: 16px; top: 58px; z-index: 5;
    background: rgba(14,14,24,.78); border: 1px solid rgba(255,255,255,.12);
    border-radius: 12px; padding: 10px 12px; font-size: 13px; line-height: 1.4; }
  #status { position: absolute; inset: 0; display: grid; place-items: center; color: #c9c6e2; }
  .hide { display: none !important; }
</style>
</head>
<body>
<div id="view">
  <div id="status">Loading panorama…</div>
  __CANVAS__
</div>
__TITLE_HTML__
<div id="chip" class="chip __CHIP_CLASS__">__DISCLOSURE__</div>
__NOTE__
<div class="bar __BAR_CLASS__">
  __CONTROLS__
</div>
<script type="application/json" id="viewer-config">__CONFIG__</script>
<script src="/viewer-assets/pano-viewer.js?v=__PANO_JS_VERSION__"></script>
</body>
</html>
"""


def _controls(asset_base: str, has_staged: bool, has_world: bool, embed: bool) -> str:
    base = escape(asset_base, quote=True)
    parts = []
    if has_staged:
        parts.append('<button type="button" data-mode="original">Original</button>')
        parts.append('<button type="button" data-mode="compare">Compare</button>')
        parts.append('<button type="button" data-mode="staged">Staged</button>')
        parts.append(
            '<span id="compare"><input id="mix" type="range" min="0" max="100" value="50" '
            'aria-label="Original to staged" /></span>'
        )
    if not embed:
        parts.append(f'<a class="btn" href="{base}/panorama.jpg?download=1">Download original</a>')
        if has_staged:
            parts.append(f'<a class="btn" href="{base}/staged.jpg?download=1">Download staged</a>')
        if has_world:
            parts.append(f'<a class="btn" href="{base}/world">Open 3D walkthrough</a>')
            if has_staged:
                parts.append(f'<a class="btn" href="{base}/world?source=staged">Open staged 3D</a>')
    return "\n".join(parts)


def _fill(values: dict[str, str]) -> str:
    """Substitute template placeholders once.

    Replacement text is not scanned again, so a project name cannot inject
    another placeholder such as __CONTROLS__.
    """

    def replace(match: re.Match[str]) -> str:
        return values.get(match.group(0), "")

    return _PLACEHOLDER.sub(replace, _PAGE)


def render_pano_page(
    *,
    title: str,
    asset_base: str,
    has_staged: bool,
    has_world: bool,
    initial: str,
    embed: bool,
    world_note: str = "",
) -> str:
    if initial not in {"original", "staged", "compare"}:
        initial = "original"
    if not has_staged:
        initial = "original"
    note = ""
    if not embed:
        note = '<div class="title">%s</div>' % escape(title)
        if world_note:
            note += '<div class="note">%s</div>' % escape(world_note)
    chip_class = "" if has_staged and initial != "original" else "hide"
    safe_title = escape(title)
    return _fill(
        {
            "__TITLE__": safe_title,
            "__CANVAS__": '<canvas id="c"></canvas>',
            "__TITLE_HTML__": note,
            "__DISCLOSURE__": escape(DISCLOSURE_TEXT),
            "__CHIP_CLASS__": chip_class,
            "__NOTE__": "",
            "__BAR_CLASS__": "hide" if embed and not has_staged else "",
            "__CONTROLS__": _controls(asset_base, has_staged, has_world, embed),
            "__CONFIG__": _config_json(
                {
                    "hasStaged": has_staged,
                    "initial": initial,
                    "kind": "pano",
                    "marble": None,
                    "stagedWorld": False,
                    "base": asset_base,
                }
            ),
            "__PANO_JS_VERSION__": PANO_JS_VERSION,
        }
    )


def render_world_page(
    *,
    title: str,
    asset_base: str,
    world_ready: bool,
    has_staged: bool,
    staged_source: bool = False,
    preview: bool = False,
    generating: bool = False,
    waiting: str = "",
) -> str:
    safe_title = escape(title)
    base = escape(asset_base, quote=True)
    canvas = '<canvas id="c"></canvas>'
    if world_ready:
        if waiting:
            lead = waiting
        elif staged_source:
            lead = "The 3D world of the virtually staged room is ready."
        elif preview:
            lead = "A fast 3D preview is ready. The full walkthrough is still generating."
        else:
            lead = "The 3D walkthrough is ready."
        note = (
            f'<div class="note">{lead} '
            f'<a class="btn" href="{base}/?view=compare">Compare panorama</a></div>'
        )
    else:
        if waiting:
            message = waiting
        elif staged_source:
            message = "360° preview of the virtually staged room. A 3D walkthrough of the staged room has not been generated yet."
        elif generating:
            message = "The 3D walkthrough is generating. This is the panorama while it runs."
        else:
            message = "360° preview of this room. A navigable 3D world has not been generated yet."
        note = (
            f'<div class="note">{message} '
            f'<a class="btn" href="{base}/?view=compare">Compare with the original panorama</a></div>'
        )
    chip_class = "" if staged_source else "hide"
    controls = f'<a class="btn" href="{base}/">Back to panorama</a>'
    if has_staged:
        if staged_source:
            controls = f'<a class="btn" href="{base}/world">Original 3D</a> ' + controls
        else:
            controls = f'<a class="btn" href="{base}/world?source=staged">Staged 3D</a> ' + controls
        controls = f'<a class="btn" href="{base}/?view=compare">Compare</a> ' + controls
    return _fill(
        {
            "__TITLE__": safe_title,
            "__CANVAS__": canvas,
            "__TITLE_HTML__": f'<div class="title">{safe_title}</div>',
            "__DISCLOSURE__": escape(DISCLOSURE_TEXT),
            "__CHIP_CLASS__": chip_class,
            "__NOTE__": note,
            "__BAR_CLASS__": "",
            "__CONTROLS__": controls,
            "__CONFIG__": _config_json(
                {
                    "hasStaged": staged_source,
                    "initial": "staged" if staged_source else "original",
                    "kind": "world",
                    "marble": None,
                    "stagedWorld": staged_source,
                    "base": asset_base,
                }
            ),
            "__PANO_JS_VERSION__": PANO_JS_VERSION,
        }
    )


_SPLAT_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover" />
<meta name="robots" content="noindex" />
<title>__TITLE__ · 3D tour</title>
<style>
  html, body { margin: 0; height: 100%; background: #0e0e18; color: #fff; overflow: hidden;
    font: 15px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; touch-action: none; }
  canvas { display: block; width: 100vw; height: 100vh; }
  .top { position: fixed; top: env(safe-area-inset-top, 0); left: 0; right: 0; padding: 12px 14px;
    display: flex; gap: 8px; align-items: center; pointer-events: none;
    background: linear-gradient(rgba(0,0,0,.55), rgba(0,0,0,0)); }
  .title { font-weight: 600; flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .btn { pointer-events: auto; color: #fff; text-decoration: none; background: rgba(255,255,255,.16);
    padding: 7px 11px; border-radius: 999px; font-size: 13px; }
  .chip { position: fixed; left: 14px; bottom: calc(env(safe-area-inset-bottom, 0) + 14px);
    background: rgba(0,0,0,.6); padding: 6px 10px; border-radius: 8px; font-size: 12px; }
  .hide { display: none; }
  #loading { position: fixed; inset: 0; display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 14px; background: #0e0e18; transition: opacity .4s; }
  #loading.done { opacity: 0; pointer-events: none; }
  .bar { width: 220px; height: 6px; border-radius: 3px; background: rgba(255,255,255,.15); overflow: hidden; }
  .fill { height: 100%; width: 0; background: #7cffb2; transition: width .15s; }
  .spinner { width: 34px; height: 34px; border-radius: 50%; border: 3px solid rgba(255,255,255,.2);
    border-top-color: #7cffb2; animation: spin 0.9s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .hint { position: fixed; bottom: calc(env(safe-area-inset-bottom, 0) + 14px); right: 14px; font-size: 12px;
    color: rgba(255,255,255,.75); background: rgba(0,0,0,.45); padding: 6px 10px; border-radius: 8px; }
</style>
<script type="importmap">
{ "imports": {
    "three": "/viewer-assets/three.module.min.js",
    "three/addons/postprocessing/Pass.js": "/viewer-assets/three-addons-Pass.js",
    "@sparkjsdev/spark": "/viewer-assets/spark-2.3.1.module.min.js"
} }
</script>
</head>
<body>
<div class="top">
  <div class="title">__TITLE__</div>
  __CONTROLS__
</div>
<div class="chip __CHIP_CLASS__">__DISCLOSURE__</div>
<div class="hint" id="hint">Drag to look · pinch or two fingers to move</div>
<div id="loading" role="status" aria-live="polite">
  <div class="spinner"></div>
  <div id="loadText">Loading 3D tour…</div>
  <div class="bar"><div class="fill" id="fill"></div></div>
</div>
<script type="application/json" id="viewer-config">__CONFIG__</script>
<script type="module" src="/viewer-assets/splat-viewer.js?v=__SPLAT_JS_VERSION__"></script>
</body>
</html>
"""


def render_splat_page(
    *,
    title: str,
    asset_base: str,
    staged_source: bool,
    has_staged: bool,
) -> str:
    """Self-hosted Spark (three.js) Gaussian-splat viewer.

    The splat comes from ``{asset_base}/world.spz`` (our private cache behind
    the share or preview token). No World Labs or CDN URL is ever in this page.
    """
    safe_title = escape(title)
    base = escape(asset_base, quote=True)
    splat = f"{asset_base}/world.spz" + ("?source=staged" if staged_source else "")
    pano = f"{asset_base}/" + ("?view=staged" if staged_source else "")
    controls = f'<a class="btn" href="{base}/">Panorama</a>'
    if has_staged:
        other = f"{base}/world" if staged_source else f"{base}/world?source=staged"
        controls = f'<a class="btn" href="{other}">{"Original 3D" if staged_source else "Staged 3D"}</a> ' + controls
    replacements = {
        "__TITLE__": safe_title,
        "__CONTROLS__": controls,
        "__CHIP_CLASS__": "" if staged_source else "hide",
        "__DISCLOSURE__": escape(DISCLOSURE_TEXT),
        "__CONFIG__": _config_json({"splat": splat, "pano": pano}),
        "__SPLAT_JS_VERSION__": SPLAT_JS_VERSION,
    }
    html = _SPLAT_PAGE
    for key, value in replacements.items():
        html = html.replace(key, value)
    return html


def _importmap_hash() -> str:
    match = re.search(r'<script type="importmap">(.*?)</script>', _SPLAT_PAGE, re.S)
    assert match, "splat page lost its importmap"
    digest = hashlib.sha256(match.group(1).encode()).digest()
    return "sha256-" + base64.b64encode(digest).decode()


# The import map is static, so CSP allows it by hash (no 'unsafe-inline').
IMPORTMAP_CSP_HASH = _importmap_hash()

"""Self-contained 360° and 3D share pages. No external scripts."""

from __future__ import annotations

import re
from html import escape

from services.disclosure import DISCLOSURE_TEXT

_PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__")

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
<script>
const HAS_STAGED = __HAS_STAGED__;
const INITIAL = __INITIAL__;
const KIND = __KIND__;
const MARBLE = __MARBLE__;
const STAGED_WORLD = __STAGED_WORLD__;
const BASE = __BASE__;
const statusEl = document.getElementById("status");
const chip = document.getElementById("chip");

function setChip(show) {
  if (!chip) return;
  chip.classList.toggle("hide", !show);
}

if (MARBLE) {
  setChip(STAGED_WORLD);
  if (statusEl) statusEl.classList.add("hide");
} else {
  const canvas = document.getElementById("c");
  const gl =
    canvas.getContext("webgl2", { antialias: true, alpha: false }) ||
    canvas.getContext("webgl", { antialias: true, alpha: false });
  if (!gl) {
    statusEl.textContent = "This browser cannot show the 360° viewer.";
  } else {
    boot(gl, canvas).catch((err) => {
      statusEl.textContent = "Could not load the panorama.";
      console.error(err);
    });
  }
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(url));
    img.src = url;
  });
}

function isPowerOfTwo(value) {
  return value > 0 && (value & (value - 1)) === 0;
}

function textureSource(gl, image) {
  const webgl2 = typeof WebGL2RenderingContext !== "undefined" && gl instanceof WebGL2RenderingContext;
  if (webgl2 || (isPowerOfTwo(image.width) && isPowerOfTwo(image.height))) return image;
  // WebGL 1 samples a non-power-of-two texture as black when wrap is REPEAT.
  const canvas2d = document.createElement("canvas");
  canvas2d.width = 1 << Math.ceil(Math.log2(image.width));
  canvas2d.height = 1 << Math.ceil(Math.log2(image.height));
  const ctx = canvas2d.getContext("2d");
  ctx.drawImage(image, 0, 0, canvas2d.width, canvas2d.height);
  return canvas2d;
}

function makeTexture(gl, image) {
  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, textureSource(gl, image));
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  return tex;
}

function mul(a, b) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      o[c * 4 + r] =
        a[r] * b[c * 4] +
        a[4 + r] * b[c * 4 + 1] +
        a[8 + r] * b[c * 4 + 2] +
        a[12 + r] * b[c * 4 + 3];
    }
  }
  return o;
}

function perspective(fovDeg, aspect) {
  const near = 0.05, far = 50;
  const f = 1 / Math.tan((fovDeg * Math.PI) / 360);
  const nf = 1 / (near - far);
  return new Float32Array([
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (far + near) * nf, -1,
    0, 0, 2 * far * near * nf, 0,
  ]);
}

function rotX(a) {
  const c = Math.cos(a), s = Math.sin(a);
  return new Float32Array([1,0,0,0, 0,c,s,0, 0,-s,c,0, 0,0,0,1]);
}
function rotY(a) {
  const c = Math.cos(a), s = Math.sin(a);
  return new Float32Array([c,0,-s,0, 0,1,0,0, s,0,c,0, 0,0,0,1]);
}

function sphere() {
  const latBands = 48, lonBands = 72;
  const positions = [], uvs = [], indices = [];
  for (let y = 0; y <= latBands; y++) {
    const v = y / latBands;
    const theta = v * Math.PI;
    for (let x = 0; x <= lonBands; x++) {
      const u = x / lonBands;
      const phi = u * Math.PI * 2;
      positions.push(Math.sin(theta) * Math.sin(phi), Math.cos(theta), Math.sin(theta) * Math.cos(phi));
      uvs.push(u, v);
    }
  }
  for (let y = 0; y < latBands; y++) {
    for (let x = 0; x < lonBands; x++) {
      const a = y * (lonBands + 1) + x;
      const b = a + lonBands + 1;
      indices.push(a, b, a + 1, b, b + 1, a + 1);
    }
  }
  return { positions: new Float32Array(positions), uvs: new Float32Array(uvs), indices: new Uint16Array(indices) };
}

async function boot(gl, canvas) {
  const original = await loadImage(BASE + "/panorama.jpg");
  let staged = null;
  if (HAS_STAGED) {
    try { staged = await loadImage(BASE + "/texture.jpg"); } catch (err) { staged = null; }
  }
  statusEl.classList.add("hide");
  const texA = makeTexture(gl, original);
  const texB = makeTexture(gl, staged || original);
  const mesh = sphere();
  const vs = gl.createShader(gl.VERTEX_SHADER);
  gl.shaderSource(vs, `
    attribute vec3 aPos; attribute vec2 aUv; uniform mat4 uMvp; varying vec2 vUv;
    void main() { vUv = aUv; gl_Position = uMvp * vec4(aPos, 1.0); }
  `);
  gl.compileShader(vs);
  const fs = gl.createShader(gl.FRAGMENT_SHADER);
  gl.shaderSource(fs, `
    precision mediump float; varying vec2 vUv; uniform sampler2D uA; uniform sampler2D uB; uniform float uMix;
    void main() { gl_FragColor = mix(texture2D(uA, vUv), texture2D(uB, vUv), uMix); }
  `);
  gl.compileShader(fs);
  const prog = gl.createProgram();
  gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog); gl.useProgram(prog);
  const bufP = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, bufP);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.positions, gl.STATIC_DRAW);
  const locP = gl.getAttribLocation(prog, "aPos");
  gl.enableVertexAttribArray(locP);
  gl.vertexAttribPointer(locP, 3, gl.FLOAT, false, 0, 0);
  const bufU = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, bufU);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.uvs, gl.STATIC_DRAW);
  const locU = gl.getAttribLocation(prog, "aUv");
  gl.enableVertexAttribArray(locU);
  gl.vertexAttribPointer(locU, 2, gl.FLOAT, false, 0, 0);
  const bufI = gl.createBuffer();
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, bufI);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);
  gl.enable(gl.DEPTH_TEST);
  gl.clearColor(0.05, 0.05, 0.09, 1);

  let yaw = 0, pitch = 0, fov = 75, mix = INITIAL === "staged" ? 1 : INITIAL === "compare" ? 0.5 : 0;
  if (!HAS_STAGED) mix = 0;
  const slider = document.getElementById("mix");
  const buttons = Array.from(document.querySelectorAll("[data-mode]"));
  function applyMode(mode) {
    if (mode === "staged" && HAS_STAGED) mix = 1;
    else if (mode === "compare" && HAS_STAGED) mix = 0.5;
    else mix = 0;
    if (slider) slider.value = String(Math.round(mix * 100));
    buttons.forEach((btn) => btn.classList.toggle("active", btn.dataset.mode === mode));
    const compareRow = document.getElementById("compare");
    if (compareRow) compareRow.classList.toggle("hide", mode !== "compare");
    setChip(HAS_STAGED && mix > 0.01);
  }
  buttons.forEach((btn) => btn.addEventListener("click", () => applyMode(btn.dataset.mode)));
  if (slider) slider.addEventListener("input", () => { mix = Number(slider.value) / 100; setChip(mix > 0.01); });
  applyMode(HAS_STAGED ? INITIAL : "original");

  let dragging = false, lastX = 0, lastY = 0, pinch = 0;
  function down(x, y) { dragging = true; lastX = x; lastY = y; }
  function move(x, y) {
    if (!dragging) return;
    yaw -= (x - lastX) * 0.005;
    pitch -= (y - lastY) * 0.005;
    pitch = Math.max(-1.25, Math.min(1.25, pitch));
    lastX = x; lastY = y;
  }
  canvas.addEventListener("pointerdown", (e) => { canvas.setPointerCapture(e.pointerId); down(e.clientX, e.clientY); });
  canvas.addEventListener("pointermove", (e) => move(e.clientX, e.clientY));
  canvas.addEventListener("pointerup", () => { dragging = false; });
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); fov = Math.max(35, Math.min(100, fov + Math.sign(e.deltaY) * 3)); }, { passive: false });
  canvas.addEventListener("touchstart", (e) => {
    if (e.touches.length === 2) pinch = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
  }, { passive: true });
  canvas.addEventListener("touchmove", (e) => {
    if (e.touches.length === 2 && pinch) {
      const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
      fov = Math.max(35, Math.min(100, fov - (d - pinch) * 0.15));
      pinch = d;
    }
  }, { passive: true });

  function frame() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.floor(canvas.clientWidth * dpr));
    const h = Math.max(1, Math.floor(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; gl.viewport(0, 0, w, h); }
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const mvp = mul(perspective(fov, w / h), mul(rotY(yaw), rotX(pitch)));
    gl.uniformMatrix4fv(gl.getUniformLocation(prog, "uMvp"), false, mvp);
    gl.uniform1f(gl.getUniformLocation(prog, "uMix"), mix);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texA);
    gl.uniform1i(gl.getUniformLocation(prog, "uA"), 0);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, texB);
    gl.uniform1i(gl.getUniformLocation(prog, "uB"), 1);
    gl.drawElements(gl.TRIANGLES, mesh.indices.length, gl.UNSIGNED_SHORT, 0);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}
</script>
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
            "__HAS_STAGED__": "true" if has_staged else "false",
            "__INITIAL__": '"' + initial + '"',
            "__KIND__": '"pano"',
            "__MARBLE__": "null",
            "__STAGED_WORLD__": "false",
            "__BASE__": '"' + escape(asset_base, quote=True) + '"',
        }
    )


def render_world_page(
    *,
    title: str,
    asset_base: str,
    marble_url: str | None,
    has_staged: bool,
    staged_source: bool = False,
    preview: bool = False,
    generating: bool = False,
    waiting: str = "",
) -> str:
    safe_title = escape(title)
    base = escape(asset_base, quote=True)
    if marble_url:
        safe_url = escape(marble_url, quote=True)
        canvas = (
            f'<iframe class="world" src="{safe_url}" allow="fullscreen; xr-spatial-tracking" '
            f'allowfullscreen title="3D world"></iframe>'
        )
        if waiting:
            lead = waiting
        elif staged_source:
            lead = "Navigable 3D world of the virtually staged room."
        elif preview:
            lead = "Fast 3D preview. The full walkthrough is still generating."
        else:
            lead = "Navigable 3D walkthrough."
        note = (
            f'<div class="note">{lead} '
            f'<a class="btn" href="{safe_url}">Open in Marble</a> '
            f'<a class="btn" href="{base}/?view=compare">Compare panorama</a></div>'
        )
        marble_js = "true"
    else:
        canvas = '<canvas id="c"></canvas>'
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
        marble_js = "null"
    chip_class = "" if staged_source else "hide"
    controls = f'<a class="btn" href="{base}/">Back to panorama</a>'
    if has_staged:
        if staged_source:
            controls = f'<a class="btn" href="{base}/world">Original 3D</a> ' + controls
        else:
            controls = f'<a class="btn" href="{base}/world?source=staged">Staged 3D</a> ' + controls
        controls = f'<a class="btn" href="{base}/?view=compare">Compare</a> ' + controls
    if marble_url:
        controls = f'<a class="btn" href="{escape(marble_url, quote=True)}">Open in Marble</a> ' + controls
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
            "__HAS_STAGED__": "true" if staged_source else "false",
            "__INITIAL__": '"staged"' if staged_source else '"original"',
            "__KIND__": '"world"',
            "__MARBLE__": marble_js,
            "__STAGED_WORLD__": "true" if staged_source else "false",
            "__BASE__": '"' + escape(asset_base, quote=True) + '"',
        }
    )

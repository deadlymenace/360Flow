// Panorama / compare viewer for share pages. Served from /viewer-assets so the
// page CSP needs no 'unsafe-inline'. Per-page values come from a JSON data block.
(() => {
const CONFIG = JSON.parse(document.getElementById("viewer-config").textContent);
const HAS_STAGED = CONFIG.hasStaged;
const INITIAL = CONFIG.initial;
const KIND = CONFIG.kind;
const MARBLE = CONFIG.marble;
const STAGED_WORLD = CONFIG.stagedWorld;
const BASE = CONFIG.base;
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
})();

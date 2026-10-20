// Self-hosted Spark splat viewer (served from /viewer-assets).
import * as THREE from "three";
import { SparkRenderer, SplatMesh, SparkControls } from "@sparkjsdev/spark";

// Per-page values come from a JSON data block (CSP: no inline scripts).
const CONFIG = JSON.parse(document.getElementById("viewer-config").textContent);
const SPLAT = CONFIG.splat;
const PANO = CONFIG.pano;
const loading = document.getElementById("loading");
const fill = document.getElementById("fill");
const text = document.getElementById("loadText");
function post(msg) {
  try { window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify(msg)); } catch (e) {}
}
const LOAD_TIMEOUT_MS = 30000;
let finished = false;
function fail(reason) {
  if (finished) return;
  finished = true;
  // Built with DOM nodes (no innerHTML); only our own relative links.
  text.textContent = "";
  const title = document.createElement("div");
  title.style.fontWeight = "600";
  title.textContent = "Can't show 3D on this device";
  const body = document.createElement("div");
  body.style.cssText = "opacity:.8;font-size:13px;margin:6px 0 12px;max-width:260px;text-align:center";
  body.textContent = reason === "webgl"
    ? "This browser doesn't support the 3D graphics the tour needs. You can still look around the 360° panorama."
    : "The 3D tour took too long to load. Check your connection and try again, or look around the 360° panorama.";
  const retry = document.createElement("a");
  retry.className = "btn";
  retry.href = "#";
  retry.textContent = "Try again";
  retry.addEventListener("click", (e) => { e.preventDefault(); location.reload(); });
  const pano = document.createElement("a");
  pano.className = "btn";
  pano.href = PANO;
  pano.textContent = "Open the 360° panorama";
  const row = document.createElement("div");
  row.style.cssText = "display:flex;gap:8px;justify-content:center";
  row.append(retry, pano);
  text.append(title, body, row);
  fill.parentElement.classList.add("hide");
  loading.querySelector(".spinner").classList.add("hide");
  loading.classList.remove("done");
  post({ type: "world-error", reason });
}

function hasWebGL2() {
  try {
    const probe = document.createElement("canvas");
    return Boolean(window.WebGL2RenderingContext && probe.getContext("webgl2"));
  } catch (e) {
    return false;
  }
}
if (!hasWebGL2()) {
  fail("webgl");
  throw new Error("WebGL2 unavailable");
}
const timer = setTimeout(() => fail("timeout"), LOAD_TIMEOUT_MS);

let renderer;
try {
  // iPhone: no MSAA (splats don't need it) and at most 2x pixels.
  renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: "high-performance" });
} catch (e) {
  clearTimeout(timer);
  fail("webgl");
  throw e;
}
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(70, window.innerWidth / window.innerHeight, 0.01, 1000);
const spark = new SparkRenderer({ renderer });
scene.add(spark);

const world = new SplatMesh({
  url: SPLAT,
  fileName: "world.spz",
  onProgress: (event) => {
    if (event.lengthComputable && event.total > 0) {
      const pct = Math.round((event.loaded / event.total) * 100);
      fill.style.width = pct + "%";
      text.textContent = "Loading 3D tour… " + pct + "%";
    } else {
      text.textContent = "Loading 3D tour… " + Math.round(event.loaded / 1048576) + " MB";
    }
  },
});
// World Labs splats are in OpenCV axes (y down, z forward): flip to three.js.
world.quaternion.set(1, 0, 0, 0);
scene.add(world);
world.initialized.then(() => {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  loading.classList.add("done");
  post({ type: "world-loaded" });
  setTimeout(() => document.getElementById("hint").classList.add("hide"), 5000);
}).catch(() => { clearTimeout(timer); fail("timeout"); });

const controls = new SparkControls({ canvas: renderer.domElement });
window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});
renderer.setAnimationLoop(() => {
  controls.update(camera);
  renderer.render(scene, camera);
});

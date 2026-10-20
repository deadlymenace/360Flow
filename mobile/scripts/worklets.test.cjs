// Guards against worklets that work on the JS thread but throw on the UI
// runtime (where an uncaught error aborts a release build: build 8 crash).
// Compiles src/ with the real babel-preset-expo (worklets plugin, production)
// and checks every worklet the way the UI runtime runs it: from its code
// string, with only its captured closure.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const babel = require('@babel/core');

const ROOT = path.resolve(__dirname, '..');
const preset = require.resolve('babel-preset-expo', { paths: [require.resolve('expo/package.json', { paths: [ROOT] })] });

function compile(file) {
  return babel.transformFileSync(file, {
    presets: [[preset]],
    babelrc: false,
    configFile: false,
    filename: file,
    caller: { name: 'metro', platform: 'ios', isDev: false, bundler: 'metro' },
    envName: 'production',
  }).code;
}

function sources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return sources(p);
    return /\.(ts|tsx)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [p] : [];
  });
}

function workletCodes(code) {
  const out = [];
  const re = /_init_data=\{code:("(?:[^"\\]|\\.)*")/g;
  let m;
  while ((m = re.exec(code))) out.push(JSON.parse(m[1]));
  return out;
}

test('no worklet default parameter reads a captured closure value', () => {
  let count = 0;
  for (const file of sources(path.join(ROOT, 'src'))) {
    const code = compile(file);
    for (const w of workletCodes(code)) {
      count += 1;
      const head = w.match(/^function\s*[\w$]*\s*\(([^)]*)\)\s*\{/);
      assert.ok(head, `unparsed worklet in ${file}`);
      const params = head[1];
      const closure = (w.match(/const\{([^}]*)\}=this\.__closure/) || [, ''])[1]
        .split(',')
        .map((s) => s.split(':')[0].trim())
        .filter(Boolean);
      for (const name of closure) {
        assert.ok(
          !new RegExp(`=[^,]*\\b${name}\\b`).test(params),
          `${path.relative(ROOT, file)}: worklet default parameter uses captured "${name}" (ReferenceError on the UI thread)`,
        );
      }
    }
  }
  assert.ok(count > 5, `expected worklets, found ${count}`);
});

test('overlay projection worklets run in a UI-runtime emulation', () => {
  // Load compiled modules (CommonJS) for orientation + its import.
  const cache = {};
  function load(rel) {
    const file = path.join(ROOT, 'src', rel);
    if (cache[file]) return cache[file].exports;
    const mod = { exports: {} };
    cache[file] = mod;
    const fn = new Function('exports', 'require', 'module', compile(file));
    fn(mod.exports, (r) => load(path.join(path.dirname(rel), r) + '.ts'), mod);
    return mod.exports;
  }
  const o = load('capture/orientation.ts');
  // Rebuild each worklet from its code string with only its closure, in a
  // fresh context (no module scope), like react-native-worklets does.
  function toUI(f) {
    if (typeof f !== 'function' || !f.__initData) return f;
    const closure = {};
    for (const [k, v] of Object.entries(f.__closure || {})) closure[k] = toUI(v);
    const fn = vm.runInNewContext(`(${f.__initData.code})`, { Math });
    return fn.bind({ __closure: closure });
  }
  const basis = { right: { x: 1, y: 0, z: 0 }, up: { x: 0, y: 0, z: 1 }, look: { x: 0, y: 1, z: 0 } };
  const yaw = 15 + toUI(o.targetYawDeg)(2);
  const p = toUI(o.projectToScreen)(toUI(o.directionFromYawPitch)(yaw, 0), basis, 390, 844, 65);
  const js = o.projectToScreen(o.directionFromYawPitch(yaw, 0), basis, 390, 844, 65);
  assert.equal(JSON.stringify(p), JSON.stringify(js));
  assert.equal(toUI(o.previewFocalPx)(390, 844, 65), o.previewFocalPx(390, 844, 65));
  assert.equal(toUI(o.wrapSigned)(350), -10);
});

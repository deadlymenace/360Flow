import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deviceAxes, AimTracker, Vec3 } from './motionFilter';
import {
  FusionState,
  basisWithHeading,
  fuseHeading,
  headingOf,
  headingRateDegPerSec,
  wrapSignedDeg,
  LoopClosure,
} from './yawFusion';
import { CAPTURE_TUNING } from './tuning';

const DEG = 180 / Math.PI;

function dot(a: Vec3, b: Vec3) {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

test('screen-right is increasing yaw, which is what the stitcher expects (+yaw = photo +x)', () => {
  // Stitcher: render_perspective(yaw=90) looks right of yaw=0, and image +x maps to +yaw.
  // App: yaw sent per shot is headingOf(look). The photo's +x is the device right axis,
  // so a point slightly to the photo's right must have a larger heading.
  for (const alpha of [0, 0.7, 2.4, -1.9]) {
    for (const beta of [1.35, Math.PI / 2, 1.8]) {
      for (const gamma of [-0.15, 0, 0.15]) {
        const a = deviceAxes(alpha, beta, gamma);
        const nudged = {
          x: a.forward.x + 0.05 * a.right.x,
          y: a.forward.y + 0.05 * a.right.y,
          z: a.forward.z + 0.05 * a.right.z,
        };
        const delta = wrapSignedDeg(headingOf(nudged) - headingOf(a.forward));
        assert.ok(delta > 1, `alpha=${alpha} beta=${beta} gamma=${gamma}: delta ${delta}`);
      }
    }
  }
});

test('gyro heading rate matches the attitude heading derivative', () => {
  // Device angular velocity from two attitudes: ω_device = vee(R0ᵀ·R1) / dt.
  const dt = 0.01;
  for (const [a0, b0, g0, da, db, dg] of [
    [0.3, 1.5, 0.05, -0.02, 0, 0],
    [1.2, 1.6, -0.1, 0.015, 0.004, -0.003],
    [-2.0, 1.4, 0.0, 0.01, -0.002, 0.006],
  ]) {
    const A = deviceAxes(a0, b0, g0);
    const B = deviceAxes(a0 + da, b0 + db, g0 + dg);
    const cols = (x: typeof A) => [x.right, x.up, { x: -x.forward.x, y: -x.forward.y, z: -x.forward.z }];
    const c0 = cols(A);
    const c1 = cols(B);
    // M = R0ᵀ R1, M[i][j] = c0[i]·c1[j]
    const m = (i: number, j: number) => dot(c0[i], c1[j]);
    const wx = (m(2, 1) - m(1, 2)) / 2 / dt;
    const wy = (m(0, 2) - m(2, 0)) / 2 / dt;
    const wz = (m(1, 0) - m(0, 1)) / 2 / dt;
    const rate = { alpha: wz * DEG, beta: wy * DEG, gamma: wx * DEG };
    const expected = wrapSignedDeg(headingOf(B.forward) - headingOf(A.forward)) / dt;
    const got = headingRateDegPerSec(rate, A);
    assert.ok(Math.abs(got - expected) < Math.max(0.5, Math.abs(expected) * 0.05), `got ${got} expected ${expected}`);
  }
});

test('lock-on replay: a magnetometer heading jump does not move the dot', () => {
  // Phone held still on a dot. 0.2 s after lock-on the attitude heading jumps 5°
  // (CoreMotion magnetometer correction); the gyro says nothing moved.
  const state: FusionState = { yaw: null };
  const tracker = new AimTracker(CAPTURE_TUNING);
  const target = 30;
  const dt = 1 / 60;
  let fired = false;
  let lostLock = false;
  let maxDrift = 0;
  for (let i = 0; i < 70; i += 1) {
    const t = i * dt;
    const att = t < 0.2 ? target + 0.5 : target + 5.5; // jump
    const noisy = att + Math.sin(i * 1.7) * 0.8; // constant magnetometer noise
    const yaw = fuseHeading(state, noisy, 0, dt, CAPTURE_TUNING.headingDriftTauSec);
    maxDrift = Math.max(maxDrift, Math.abs(yaw - (target + 0.5 + Math.sin(0) * 0.8)));
    const s = tracker.update({
      angleDeg: Math.abs(yaw - target),
      rateDegPerSec: 0,
      dtMs: dt * 1000,
      blocked: false,
      targetChanged: false,
    });
    if (i > 2 && !s.locked) lostLock = true;
    if (s.fire) fired = true;
  }
  assert.ok(maxDrift < 1.5, `fused heading moved ${maxDrift}°`);
  assert.equal(lostLock, false);
  assert.equal(fired, true);
});

test('fused heading follows real turns from the gyro and removes slow drift', () => {
  const state: FusionState = { yaw: null };
  const dt = 1 / 60;
  let yaw = 0;
  // Turn right 45° over 1 s; attitude agrees.
  for (let i = 0; i <= 60; i += 1) {
    yaw = fuseHeading(state, (45 * i) / 60, 45, dt, CAPTURE_TUNING.headingDriftTauSec);
  }
  assert.ok(Math.abs(yaw - 45) < 1.5, `${yaw}`);
  // Gyro bias of 1°/s while still for 20 s: drift stays bounded.
  for (let i = 0; i < 1200; i += 1) {
    yaw = fuseHeading(state, 45, 1, dt, CAPTURE_TUNING.headingDriftTauSec);
  }
  assert.ok(Math.abs(yaw - 45) < CAPTURE_TUNING.headingDriftTauSec * 1 + 0.5, `${yaw}`);
});

test('basisWithHeading keeps pitch and sets heading', () => {
  const a = deviceAxes(0.4, 1.45, 0.05);
  const b = basisWithHeading(a, 77);
  assert.ok(Math.abs(wrapSignedDeg(headingOf(b.look) - 77)) < 1e-6);
  assert.ok(Math.abs(b.look.z - a.forward.z) < 1e-9);
});

test('compass correction is a slow blend, never a snap', () => {
  const state: FusionState = { yaw: null };
  fuseHeading(state, 0, 0, 0, 6);
  const next = fuseHeading(state, 40, 0, 1 / 60, 6); // compass leaps 40°
  assert.ok(Math.abs(next) < 0.5, `${next}`);
});

test('loop closure flags drift above 3°', () => {
  const dt = 1 / 60;
  const good = new LoopClosure();
  good.start(10);
  const bad = new LoopClosure();
  bad.start(10);
  for (let i = 1; i <= 720; i += 1) {
    const att = 10 + (360 * i) / 720; // a full turn in 12 s
    good.update(att, 30 + 0.1, dt); // gyro bias 0.1°/s -> 1.2° drift
    bad.update(att, 30 + 0.5, dt); // gyro bias 0.5°/s -> 6° drift
  }
  assert.ok(good.result().pass, String(good.result().errorDeg));
  assert.equal(bad.result().pass, false);
  assert.ok(Math.abs(bad.result().errorDeg - 6) < 0.5, String(bad.result().errorDeg));
});

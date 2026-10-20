import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  AimTracker,
  Cooldown,
  OneEuroVec3,
  RateFilter,
  YawUnwrapper,
  nextDotReady,
  angleDeg,
  basisFromForwardUp,
  deviceAxes,
  manualShotAllowed,
  yawPitchFromForward,
} from './motionFilter';
import { CAPTURE_TUNING } from './tuning';

const RAD = Math.PI / 180;

// Deterministic pseudo-random tremor.
function rng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296 - 0.5;
  };
}

test('upright phone: Euler alpha swings but forward-vector yaw does not', () => {
  // At beta = 90° (gimbal lock) alpha and gamma can move together with no real rotation.
  const a = yawPitchFromForward(deviceAxes(0.3, Math.PI / 2, 0.2).forward);
  const b = yawPitchFromForward(deviceAxes(0.3 + 1.2, Math.PI / 2, 0.2 + 1.2).forward);
  assert.ok(Math.abs(a.yawDeg - b.yawDeg) < 1e-6, `${a.yawDeg} vs ${b.yawDeg}`);
  assert.ok(Math.abs(a.pitchDeg) < 1e-6);
});

test('upright phone with small tremor: filtered yaw stays within a degree', () => {
  const noise = rng(7);
  const filter = new OneEuroVec3();
  const yaws: number[] = [];
  const rawYaws: number[] = [];
  for (let i = 0; i < 180; i += 1) {
    // ±0.6° hand tremor on every axis, plus Euler alpha/gamma swapping near the pole.
    const swap = noise() * 40 * RAD;
    const alpha = 0.5 + swap + noise() * 1.2 * RAD;
    const beta = Math.PI / 2 - Math.abs(noise()) * 1.2 * RAD;
    const gamma = 0.1 + swap + noise() * 1.2 * RAD;
    const raw = deviceAxes(alpha, beta, gamma).forward;
    rawYaws.push(yawPitchFromForward(raw).yawDeg);
    const smooth = filter.filter(raw, i / 60);
    if (i > 30) yaws.push(yawPitchFromForward(smooth).yawDeg);
  }
  const spread = Math.max(...yaws) - Math.min(...yaws);
  const rawSpread = Math.max(...rawYaws) - Math.min(...rawYaws);
  assert.ok(spread < 1, `filtered yaw spread ${spread.toFixed(2)}°`);
  assert.ok(spread < rawSpread, 'filter reduces jitter');
});

test('one-euro filter follows a real turn', () => {
  const filter = new OneEuroVec3();
  let out = { x: 0, y: 1, z: 0 };
  for (let i = 0; i <= 60; i += 1) {
    const yaw = i * 1.5 * RAD; // 90°/s for 1 s
    out = filter.filter({ x: Math.sin(yaw), y: Math.cos(yaw), z: 0 }, i / 60);
  }
  const lag = 90 - yawPitchFromForward(out).yawDeg;
  assert.ok(lag >= 0 && lag < 10, `lag ${lag.toFixed(1)}°`);
});

test('yaw unwrapping has no jump at 360 → 0', () => {
  const u = new YawUnwrapper();
  const out = [358, 359, 0.5, 2, 1, 359.5].map((y) => u.update(y));
  for (let i = 1; i < out.length; i += 1) assert.ok(Math.abs(out[i] - out[i - 1]) < 5, out.join(','));
});

test('aim hysteresis: enter inside enterDeg, stay locked until beyond exitDeg', () => {
  const aim = new AimTracker();
  const step = (angle: number, rate = 0) =>
    aim.update({ angleDeg: angle, rateDegPerSec: rate, dtMs: 16, blocked: false });
  assert.equal(step(CAPTURE_TUNING.aimEnterDeg + 0.5).locked, false);
  const on = step(CAPTURE_TUNING.aimEnterDeg - 0.5);
  assert.equal(on.locked, true);
  assert.equal(on.lockedOn, true);
  // Tremor between enter and exit keeps the lock.
  for (const a of [5, 6.5, 4.5, 6.9]) assert.equal(step(a).locked, true);
  assert.equal(step(CAPTURE_TUNING.aimExitDeg + 0.5).locked, false);
});

test('auto-capture needs a steady hold; shaking drains instead of resetting', () => {
  const aim = new AimTracker();
  let fired = 0;
  let t = 0;
  while (t < CAPTURE_TUNING.holdMs - 50) {
    if (aim.update({ angleDeg: 2, rateDegPerSec: 3, dtMs: 16, blocked: false }).fire) fired += 1;
    t += 16;
  }
  assert.equal(fired, 0);
  const shaky = aim.update({ angleDeg: 2, rateDegPerSec: 40, dtMs: 16, blocked: false });
  assert.equal(shaky.steady, false);
  assert.ok(shaky.progress > 0.5, 'one shaky sample does not wipe the hold');
  let state = shaky;
  for (let i = 0; i < 20 && !state.fire; i += 1) {
    state = aim.update({ angleDeg: 2, rateDegPerSec: 3, dtMs: 16, blocked: false });
  }
  assert.equal(state.fire, true);
});

test('blocked (tilted) never fires', () => {
  const aim = new AimTracker();
  for (let i = 0; i < 100; i += 1) {
    assert.equal(aim.update({ angleDeg: 0, rateDegPerSec: 0, dtMs: 16, blocked: true }).fire, false);
  }
});

test('rate filter smooths a single spike', () => {
  const f = new RateFilter(120);
  f.update(2, 16);
  assert.ok(f.update(80, 16) < 15);
});

test('manual shutter: wider ring allowed, tilt over the limit blocked', () => {
  assert.equal(manualShotAllowed(10, 0), true);
  assert.equal(manualShotAllowed(CAPTURE_TUNING.manualRingDeg + 1, 0), false);
  assert.equal(manualShotAllowed(2, CAPTURE_TUNING.tiltLimitDeg + 1), false);
});

test('basis from smoothed forward/up is orthonormal and matches the device axes', () => {
  const axes = deviceAxes(0.7, 1.3, -0.2);
  const b = basisFromForwardUp(axes.forward, axes.up);
  assert.ok(angleDeg(b.right, axes.right) < 1e-3);
  assert.ok(angleDeg(b.up, axes.up) < 1e-3);
});

test('haptic cooldown: at most one buzz per cooldown window', () => {
  const c = new Cooldown(CAPTURE_TUNING.hapticCooldownMs);
  assert.equal(c.tryFire(1000), true);
  assert.equal(c.tryFire(1100), false);
  assert.equal(c.tryFire(1599), false);
  assert.equal(c.tryFire(1600), true);
});

test('tuning: enter 3°/exit 7°, hold 1 s, steady 7°/s, drain 3, shot cooldown 800 ms', () => {
  assert.equal(CAPTURE_TUNING.aimEnterDeg, 3);
  assert.equal(CAPTURE_TUNING.aimExitDeg, 7);
  assert.equal(CAPTURE_TUNING.holdMs, 1000);
  assert.equal(CAPTURE_TUNING.steadyMaxDegPerSec, 7);
  assert.equal(CAPTURE_TUNING.holdDrainRate, 3);
  assert.equal(CAPTURE_TUNING.shotCooldownMs, 800);
  assert.equal(CAPTURE_TUNING.nextDotMinMoveDeg, 10);
  assert.equal(CAPTURE_TUNING.debugAimLog, false);
  assert.equal(CAPTURE_TUNING.hapticCooldownMs, 600);
  assert.ok(CAPTURE_TUNING.aimExitDeg > CAPTURE_TUNING.aimEnterDeg);
});

test('next dot waits for the cooldown and a 10° turn', () => {
  assert.equal(nextDotReady(1000, null, null), true);
  assert.equal(nextDotReady(1500, 1000, 30), false); // cooling down
  assert.equal(nextDotReady(1900, 1000, 4), false); // has not turned yet
  assert.equal(nextDotReady(1900, 1000, 12), true);
});

test('hold needs a full second of steady aim; motion over 7°/s drains it', () => {
  const tracker = new AimTracker();
  let fired = false;
  for (let i = 0; i < 50; i += 1) {
    const s = tracker.update({ angleDeg: 1, rateDegPerSec: 3, dtMs: 16, blocked: false, targetChanged: false });
    fired = fired || s.fire;
  }
  assert.equal(fired, false, 'fired before 1 s');
  for (let i = 0; i < 20; i += 1) tracker.update({ angleDeg: 1, rateDegPerSec: 20, dtMs: 16, blocked: false, targetChanged: false });
  let t = 0;
  while (!tracker.update({ angleDeg: 1, rateDegPerSec: 3, dtMs: 16, blocked: false, targetChanged: false }).fire) t += 16;
  assert.ok(t > 300, `refilled too fast: ${t} ms`);
});

// Gyro-integrated heading with a slow pull toward the attitude heading.
//
// Expo DeviceMotion on iOS uses CoreMotion's xMagneticNorthZVertical frame
// (expo-sensors SensorsUtils.swift), so `rotation.alpha` is corrected by the
// magnetometer. Indoors that correction shows up as constant 2–5 px dot shake
// and occasional several-degree jumps right after lock-on. The gyro is smooth
// over seconds, so short-term heading comes from integrating rotationRate
// about world vertical, and the attitude heading only removes long-term drift.
//
// Pure functions marked 'worklet' so they run on the Reanimated UI thread
// and in `npm test` (Node ignores the directive).

import type { Vec3 } from './motionFilter';

const DEG = 180 / Math.PI;

export type Basis = { right: Vec3; up: Vec3; look: Vec3 };

export function wrapSignedDeg(deg: number): number {
  'worklet';
  const w = ((deg % 360) + 360) % 360;
  return w > 180 ? w - 360 : w;
}

/** Heading of a direction, degrees, increasing when turning right (see headingFromLook). */
export function headingOf(v: Vec3): number {
  'worklet';
  return Math.atan2(v.x, v.y) * DEG;
}

/**
 * Angular speed about world vertical, rad/s, in heading units: positive when
 * turning right. Expo rotationRate is deg/s in device axes (alpha = z, beta = y,
 * gamma = x, per DeviceMotionModule.swift). The world-up direction in device
 * coordinates is the z component of each device axis, and the camera looks
 * along device -Z, so heading rate = -(ω · up_device).
 */
export function headingRateDegPerSec(
  rate: { alpha: number; beta: number; gamma: number },
  axes: { right: Vec3; up: Vec3; forward: Vec3 },
): number {
  'worklet';
  // deviceZ = -forward.
  const vertical = rate.gamma * axes.right.z + rate.beta * axes.up.z + rate.alpha * -axes.forward.z;
  return -vertical;
}

export type FusionState = { yaw: number | null };

/**
 * One fusion step. `attHeadingDeg` is the (noisy, magnetometer-corrected)
 * heading from the attitude; `gyroRateDegPerSec` from `headingRateDegPerSec`.
 * `tauSec` is the drift-correction time constant: long enough that a sudden
 * magnetometer correction moves the result only a few percent per second.
 * Returns an unwrapped heading (no jump at 359°→0°).
 */
export function fuseHeading(
  state: FusionState,
  attHeadingDeg: number,
  gyroRateDegPerSec: number,
  dtSec: number,
  tauSec: number,
): number {
  'worklet';
  if (state.yaw == null) {
    state.yaw = attHeadingDeg;
    return attHeadingDeg;
  }
  const dt = Math.min(Math.max(dtSec, 0), 0.1);
  let yaw = state.yaw + gyroRateDegPerSec * dt;
  const error = wrapSignedDeg(attHeadingDeg - yaw);
  // A large disagreement is a real reset (app resumed, frame re-zeroed): snap.
  if (Math.abs(error) > 25) {
    yaw += error;
  } else {
    yaw += error * Math.min(1, dt / Math.max(tauSec, 1e-3));
  }
  state.yaw = yaw;
  return yaw;
}

/** Rotate a vector about world vertical so its heading grows by `deg`. */
export function rotateHeading(v: Vec3, deg: number): Vec3 {
  'worklet';
  const r = deg / DEG;
  const c = Math.cos(r);
  const s = Math.sin(r);
  return { x: v.x * c + v.y * s, y: -v.x * s + v.y * c, z: v.z };
}

/** The attitude basis with its heading replaced by `headingDeg` (pitch and roll untouched). */
export function basisWithHeading(axes: { right: Vec3; up: Vec3; forward: Vec3 }, headingDeg: number): Basis {
  'worklet';
  const delta = headingDeg - headingOf(axes.forward);
  return {
    right: rotateHeading(axes.right, delta),
    up: rotateHeading(axes.up, delta),
    look: rotateHeading(axes.forward, delta),
  };
}

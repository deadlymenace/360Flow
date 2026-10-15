import { SEGMENT_ANGLE, SEGMENT_COUNT } from '../models/types';

/** Angular distance from the reticle at which a dot counts as aimed. */
export const AIM_TOLERANCE_DEG = 4;
/** Manual shutter still accepts the nearest dot inside half a step. */
export const MANUAL_TOLERANCE_DEG = SEGMENT_ANGLE / 2;
/** Rotation slower than this, held for AIM_HOLD_MS, starts a capture. */
export const STEADY_MAX_DEG_PER_SEC = 14;
export const AIM_HOLD_MS = 420;

export type Vec3 = { x: number; y: number; z: number };
export type Quat = { w: number; x: number; y: number; z: number };

export type Attitude = {
  /** CoreMotion yaw in degrees, the same heading the stitcher already receives. */
  yawDeg: number;
  pitchRad: number;
  rollRad: number;
};

export type CameraBasis = {
  right: Vec3;
  up: Vec3;
  look: Vec3;
};

export type ScreenPoint = {
  inView: boolean;
  /** Pixel position. Outside the screen when the target is in front but out of frame. */
  x: number;
  y: number;
  /** Camera-plane direction, used to point at targets behind the phone. */
  planeX: number;
  planeY: number;
};

const DEG = 180 / Math.PI;

export function wrap360(deg: number): number {
  return ((deg % 360) + 360) % 360;
}

export function wrapSigned(deg: number): number {
  const wrapped = wrap360(deg);
  return wrapped > 180 ? wrapped - 360 : wrapped;
}

export function targetYawDeg(index: number): number {
  return index * SEGMENT_ANGLE;
}

/** Horizon direction whose heading matches CoreMotion yaw. Pitch above the horizon is degrees. */
export function directionFromYawPitch(yawDeg: number, pitchDeg = 0): Vec3 {
  const yaw = (yawDeg * Math.PI) / 180;
  const pitch = (pitchDeg * Math.PI) / 180;
  const cosP = Math.cos(pitch);
  return {
    x: Math.sin(yaw) * cosP,
    y: Math.cos(yaw) * cosP,
    z: Math.sin(pitch),
  };
}

/**
 * Device axes in the CoreMotion reference frame (Z up).
 * Columns of Apple's attitude matrix: device +X, +Y, and +Z (out of the screen).
 * The back camera looks along -Z.
 */
export function cameraBasis(yawRad: number, pitchRad: number, rollRad: number): CameraBasis {
  const cy = Math.cos(yawRad);
  const sy = Math.sin(yawRad);
  const cp = Math.cos(pitchRad);
  const sp = Math.sin(pitchRad);
  const cr = Math.cos(rollRad);
  const sr = Math.sin(rollRad);
  const right = {
    x: cy * cr + sy * sp * sr,
    y: -sy * cr + cy * sp * sr,
    z: -cp * sr,
  };
  const up = { x: sy * cp, y: cy * cp, z: sp };
  const deviceZ = {
    x: cy * sr - sy * sp * cr,
    y: -sy * sr - cy * sp * cr,
    z: cp * cr,
  };
  return {
    right,
    up,
    look: { x: -deviceZ.x, y: -deviceZ.y, z: -deviceZ.z },
  };
}

export function dot(a: Vec3, b: Vec3): number {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

export function angleBetweenDeg(a: Vec3, b: Vec3): number {
  const c = Math.min(1, Math.max(-1, dot(a, b)));
  return Math.acos(c) * DEG;
}

export function projectToScreen(
  target: Vec3,
  basis: CameraBasis,
  width: number,
  height: number,
  hfovDeg: number,
): ScreenPoint {
  const x = dot(target, basis.right);
  const y = dot(target, basis.up);
  const z = dot(target, basis.look);
  const focal = width / 2 / Math.tan(((hfovDeg * Math.PI) / 180) / 2);
  const planeX = x;
  const planeY = -y;
  if (z <= 0.08) {
    return { inView: false, x: width / 2, y: height / 2, planeX, planeY };
  }
  const sx = width / 2 + (x / z) * focal;
  const sy = height / 2 - (y / z) * focal;
  const inView = sx >= 8 && sx <= width - 8 && sy >= 8 && sy <= height - 8;
  return { inView, x: sx, y: sy, planeX, planeY };
}

/** Push an off-screen direction onto the inset edges of the preview. */
export function edgePoint(
  dirX: number,
  dirY: number,
  width: number,
  height: number,
  inset: { top: number; right: number; bottom: number; left: number },
): { x: number; y: number; angle: number } {
  const cx = width / 2;
  const cy = height / 2;
  const dx = dirX;
  const dy = dirY;
  const angle = Math.atan2(dy, dx);
  const left = inset.left;
  const right = width - inset.right;
  const top = inset.top;
  const bottom = height - inset.bottom;
  let best = Infinity;
  let x = cx;
  let y = cy;
  if (Math.abs(dx) > 1e-4) {
    const tRight = (right - cx) / dx;
    const yRight = cy + tRight * dy;
    if (tRight > 0 && yRight >= top && yRight <= bottom && tRight < best) {
      best = tRight;
      x = right;
      y = yRight;
    }
    const tLeft = (left - cx) / dx;
    const yLeft = cy + tLeft * dy;
    if (tLeft > 0 && yLeft >= top && yLeft <= bottom && tLeft < best) {
      best = tLeft;
      x = left;
      y = yLeft;
    }
  }
  if (Math.abs(dy) > 1e-4) {
    const tBottom = (bottom - cy) / dy;
    const xBottom = cx + tBottom * dx;
    if (tBottom > 0 && xBottom >= left && xBottom <= right && tBottom < best) {
      best = tBottom;
      x = xBottom;
      y = bottom;
    }
    const tTop = (top - cy) / dy;
    const xTop = cx + tTop * dx;
    if (tTop > 0 && xTop >= left && xTop <= right && tTop < best) {
      x = xTop;
      y = top;
    }
  }
  return { x, y, angle };
}

/** Quaternion whose basis matches `cameraBasis` for the same Euler angles. */
export function quatFromAttitude(yawRad: number, pitchRad: number, rollRad: number): Quat {
  const basis = cameraBasis(yawRad, pitchRad, rollRad);
  const r = basis.right;
  const u = basis.up;
  const z = { x: -basis.look.x, y: -basis.look.y, z: -basis.look.z };
  const trace = r.x + u.y + z.z;
  if (trace > 0) {
    const s = Math.sqrt(trace + 1) * 2;
    return normalizeQuat({
      w: 0.25 * s,
      x: (u.z - z.y) / s,
      y: (z.x - r.z) / s,
      z: (r.y - u.x) / s,
    });
  }
  if (r.x > u.y && r.x > z.z) {
    const s = Math.sqrt(1 + r.x - u.y - z.z) * 2;
    return normalizeQuat({
      w: (u.z - z.y) / s,
      x: 0.25 * s,
      y: (u.x + r.y) / s,
      z: (z.x + r.z) / s,
    });
  }
  if (u.y > z.z) {
    const s = Math.sqrt(1 + u.y - r.x - z.z) * 2;
    return normalizeQuat({
      w: (z.x - r.z) / s,
      x: (u.x + r.y) / s,
      y: 0.25 * s,
      z: (z.y + u.z) / s,
    });
  }
  const s = Math.sqrt(1 + z.z - r.x - u.y) * 2;
  return normalizeQuat({
    w: (r.y - u.x) / s,
    x: (z.x + r.z) / s,
    y: (z.y + u.z) / s,
    z: 0.25 * s,
  });
}

export function slerpQuat(a: Quat, b: Quat, t: number): Quat {
  let bx = b.x;
  let by = b.y;
  let bz = b.z;
  let bw = b.w;
  let cos = a.w * bw + a.x * bx + a.y * by + a.z * bz;
  if (cos < 0) {
    cos = -cos;
    bx = -bx;
    by = -by;
    bz = -bz;
    bw = -bw;
  }
  if (cos > 0.9995) {
    return normalizeQuat({
      w: a.w + (bw - a.w) * t,
      x: a.x + (bx - a.x) * t,
      y: a.y + (by - a.y) * t,
      z: a.z + (bz - a.z) * t,
    });
  }
  const theta = Math.acos(Math.min(1, cos));
  const sin = Math.sin(theta);
  const wa = Math.sin((1 - t) * theta) / sin;
  const wb = Math.sin(t * theta) / sin;
  return {
    w: a.w * wa + bw * wb,
    x: a.x * wa + bx * wb,
    y: a.y * wa + by * wb,
    z: a.z * wa + bz * wb,
  };
}

export function normalizeQuat(q: Quat): Quat {
  const n = Math.hypot(q.w, q.x, q.y, q.z) || 1;
  return { w: q.w / n, x: q.x / n, y: q.y / n, z: q.z / n };
}

/** Rotation of device axes into the world, matching `cameraBasis`. */
export function basisFromQuat(q: Quat): CameraBasis {
  const { w, x, y, z } = normalizeQuat(q);
  const right = {
    x: 1 - 2 * (y * y + z * z),
    y: 2 * (x * y + w * z),
    z: 2 * (x * z - w * y),
  };
  const up = {
    x: 2 * (x * y - w * z),
    y: 1 - 2 * (x * x + z * z),
    z: 2 * (y * z + w * x),
  };
  const deviceZ = {
    x: 2 * (x * z + w * y),
    y: 2 * (y * z - w * x),
    z: 1 - 2 * (x * x + y * y),
  };
  return {
    right,
    up,
    look: { x: -deviceZ.x, y: -deviceZ.y, z: -deviceZ.z },
  };
}

export function rotationRateDegPerSec(rate: { alpha: number; beta: number; gamma: number } | null): number {
  if (!rate) return 0;
  return Math.hypot(rate.alpha, rate.beta, rate.gamma);
}

export type TargetProjection = {
  index: number;
  yawDeg: number;
  inView: boolean;
  x: number;
  y: number;
  planeX: number;
  planeY: number;
  angleDeg: number;
};

export function projectTargets(
  basis: CameraBasis,
  originYawDeg: number,
  width: number,
  height: number,
  hfovDeg: number,
): TargetProjection[] {
  const targets: TargetProjection[] = [];
  for (let index = 0; index < SEGMENT_COUNT; index += 1) {
    const yawDeg = wrap360(originYawDeg + targetYawDeg(index));
    const direction = directionFromYawPitch(yawDeg, 0);
    const point = projectToScreen(direction, basis, width, height, hfovDeg);
    targets.push({
      index,
      yawDeg,
      inView: point.inView,
      x: point.x,
      y: point.y,
      planeX: point.planeX,
      planeY: point.planeY,
      angleDeg: angleBetweenDeg(basis.look, direction),
    });
  }
  return targets;
}

export function nearestTarget(targets: TargetProjection[], captured: boolean[]): TargetProjection | null {
  let best: TargetProjection | null = null;
  for (const target of targets) {
    if (captured[target.index]) continue;
    if (!best || target.angleDeg < best.angleDeg) best = target;
  }
  return best;
}

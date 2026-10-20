// Orientation filtering and aim logic for the capture screen.
// Pure TypeScript (no React, no native imports) so `npm test` can cover it.

import { CAPTURE_TUNING } from './tuning';

export type Vec3 = { x: number; y: number; z: number };

const DEG = 180 / Math.PI;

export function vdot(a: Vec3, b: Vec3): number {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}
export function vcross(a: Vec3, b: Vec3): Vec3 {
  return { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x };
}
export function vnorm(a: Vec3): Vec3 {
  const n = Math.hypot(a.x, a.y, a.z) || 1;
  return { x: a.x / n, y: a.y / n, z: a.z / n };
}
export function angleDeg(a: Vec3, b: Vec3): number {
  return Math.acos(Math.min(1, Math.max(-1, vdot(vnorm(a), vnorm(b))))) * DEG;
}

/**
 * Device axes in the CoreMotion world frame (Z up) from DeviceMotion's
 * alpha/beta/gamma. The rotation matrix is always well-defined, even where the
 * Euler angles themselves are not (phone upright, beta ≈ 90°): there alpha and
 * gamma can swing together while the matrix, and so the forward vector, stays put.
 * Same convention as `cameraBasis` in orientation.ts (validated on device).
 */
export function deviceAxes(alpha: number, beta: number, gamma: number): { right: Vec3; up: Vec3; forward: Vec3 } {
  const cy = Math.cos(alpha);
  const sy = Math.sin(alpha);
  const cp = Math.cos(beta);
  const sp = Math.sin(beta);
  const cr = Math.cos(gamma);
  const sr = Math.sin(gamma);
  const right = { x: cy * cr + sy * sp * sr, y: -sy * cr + cy * sp * sr, z: -cp * sr };
  const up = { x: sy * cp, y: cy * cp, z: sp };
  // Back camera looks along device -Z.
  const forward = { x: -(cy * sr - sy * sp * cr), y: -(-sy * sr - cy * sp * cr), z: -(cp * cr) };
  return { right, up, forward };
}

/** Yaw is atan2 of the forward vector's horizontal components; pitch is its elevation. */
export function yawPitchFromForward(forward: Vec3): { yawDeg: number; pitchDeg: number } {
  const f = vnorm(forward);
  const yaw = Math.atan2(f.x, f.y) * DEG;
  return {
    yawDeg: ((yaw % 360) + 360) % 360,
    pitchDeg: Math.asin(Math.min(1, Math.max(-1, f.z))) * DEG,
  };
}

/** Turns a 0–360 yaw stream into a continuous one (no jump at the 359°→0° wrap). */
export class YawUnwrapper {
  private last: number | null = null;
  private offset = 0;
  update(yawDeg: number): number {
    if (this.last != null) {
      const delta = yawDeg - this.last;
      if (delta > 180) this.offset -= 360;
      else if (delta < -180) this.offset += 360;
    }
    this.last = yawDeg;
    return yawDeg + this.offset;
  }
  reset(): void {
    this.last = null;
    this.offset = 0;
  }
}

function smoothingAlpha(cutoffHz: number, dtSec: number): number {
  const tau = 1 / (2 * Math.PI * cutoffHz);
  return 1 / (1 + tau / dtSec);
}

/** One-euro filter on a unit vector: smooth when still, responsive when turning. */
export class OneEuroVec3 {
  private x: Vec3 | null = null;
  private dx: Vec3 = { x: 0, y: 0, z: 0 };
  private lastT: number | null = null;
  constructor(
    private minCutoffHz: number = CAPTURE_TUNING.oneEuro.minCutoffHz,
    private beta: number = CAPTURE_TUNING.oneEuro.beta,
    private dCutoffHz: number = CAPTURE_TUNING.oneEuro.dCutoffHz,
  ) {}

  filter(value: Vec3, tSec: number): Vec3 {
    if (this.x == null || this.lastT == null) {
      this.x = vnorm(value);
      this.lastT = tSec;
      return this.x;
    }
    const dt = Math.max(1e-3, tSec - this.lastT);
    this.lastT = tSec;
    const raw = { x: (value.x - this.x.x) / dt, y: (value.y - this.x.y) / dt, z: (value.z - this.x.z) / dt };
    const ad = smoothingAlpha(this.dCutoffHz, dt);
    this.dx = {
      x: this.dx.x + ad * (raw.x - this.dx.x),
      y: this.dx.y + ad * (raw.y - this.dx.y),
      z: this.dx.z + ad * (raw.z - this.dx.z),
    };
    const speed = Math.hypot(this.dx.x, this.dx.y, this.dx.z);
    const a = smoothingAlpha(this.minCutoffHz + this.beta * speed, dt);
    this.x = vnorm({
      x: this.x.x + a * (value.x - this.x.x),
      y: this.x.y + a * (value.y - this.x.y),
      z: this.x.z + a * (value.z - this.x.z),
    });
    return this.x;
  }

  reset(): void {
    this.x = null;
    this.lastT = null;
    this.dx = { x: 0, y: 0, z: 0 };
  }
}

/**
 * Soft dead zone: the shown vector stays still until the filtered one is more
 * than `deadZoneDeg` away, then follows it, trailing by the dead-zone angle.
 */
export function applyDeadZone(shown: Vec3 | null, target: Vec3, deadZoneDeg: number = CAPTURE_TUNING.deadZoneDeg): Vec3 {
  if (!shown) return vnorm(target);
  const angle = angleDeg(shown, target);
  if (angle <= deadZoneDeg) return shown;
  const t = (angle - deadZoneDeg) / angle;
  return vnorm({
    x: shown.x + (target.x - shown.x) * t,
    y: shown.y + (target.y - shown.y) * t,
    z: shown.z + (target.z - shown.z) * t,
  });
}

/** Re-orthogonalise a smoothed up vector against a smoothed forward vector. */
export function basisFromForwardUp(forward: Vec3, upHint: Vec3): { right: Vec3; up: Vec3; look: Vec3 } {
  const look = vnorm(forward);
  const up = vnorm({
    x: upHint.x - vdot(upHint, look) * look.x,
    y: upHint.y - vdot(upHint, look) * look.y,
    z: upHint.z - vdot(upHint, look) * look.z,
  });
  // Device X = Y × Z, and Z = -look, so right = look × up.
  const right = vnorm(vcross(look, up));
  return { right, up, look };
}

/** First-order low-pass on the gyro speed so one spiky sample does not reset a hold. */
export class RateFilter {
  private value: number | null = null;
  constructor(private timeConstantMs: number = CAPTURE_TUNING.rateSmoothingMs) {}
  update(rate: number, dtMs: number): number {
    if (this.value == null) {
      this.value = rate;
      return rate;
    }
    const a = dtMs / (this.timeConstantMs + dtMs);
    this.value += a * (rate - this.value);
    return this.value;
  }
  reset(): void {
    this.value = null;
  }
}

export type AimInput = {
  /** Angle from the raw (unfiltered) forward vector to the target dot. */
  angleDeg: number;
  /** Low-passed gyro speed. */
  rateDegPerSec: number;
  dtMs: number;
  /** A capture is blocked right now (tilted, already capturing, no open dot). */
  blocked: boolean;
  /** Target changed since the last update; drop any lock. */
  targetChanged?: boolean;
};

export type AimState = {
  locked: boolean;
  /** 0–1 hold progress for the ring. */
  progress: number;
  steady: boolean;
  /** True on the update the lock was gained (play a haptic). */
  lockedOn: boolean;
  /** True once on the update the hold completes (take the photo). */
  fire: boolean;
};

/** Aim hysteresis and the steady hold that gates auto-capture. */
export class AimTracker {
  private locked = false;
  private heldMs = 0;
  constructor(private tuning = CAPTURE_TUNING) {}

  update(input: AimInput): AimState {
    const t = this.tuning;
    const steady = input.rateDegPerSec <= t.steadyMaxDegPerSec;
    if (input.targetChanged || input.blocked) {
      this.locked = false;
      this.heldMs = 0;
      return { locked: false, progress: 0, steady, lockedOn: false, fire: false };
    }
    const wasLocked = this.locked;
    if (this.locked) {
      if (input.angleDeg > t.aimExitDeg) this.locked = false;
    } else if (input.angleDeg <= t.aimEnterDeg) {
      this.locked = true;
    }
    if (!this.locked) {
      this.heldMs = 0;
    } else if (steady) {
      this.heldMs += input.dtMs;
    } else {
      this.heldMs = Math.max(0, this.heldMs - input.dtMs * t.holdDrainRate);
    }
    let fire = false;
    if (this.locked && this.heldMs >= t.holdMs) {
      fire = true;
      this.heldMs = 0;
    }
    return {
      locked: this.locked,
      progress: fire ? 1 : Math.min(1, this.heldMs / t.holdMs),
      steady,
      lockedOn: this.locked && !wasLocked,
      fire,
    };
  }

  reset(): void {
    this.locked = false;
    this.heldMs = 0;
  }
}

/** Manual shutter: allowed inside the wider ring of the current dot, never when tilted. */
export function manualShotAllowed(angleToCurrentDeg: number, pitchDeg: number, tuning = CAPTURE_TUNING): boolean {
  if (Math.abs(pitchDeg) > tuning.tiltLimitDeg) return false;
  return angleToCurrentDeg <= tuning.manualRingDeg;
}

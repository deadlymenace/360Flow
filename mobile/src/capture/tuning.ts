// Every capture tunable in one place. Values are first guesses: tune on a
// real iPhone (TestFlight) and keep the comments in sync with what you change.

export const CAPTURE_TUNING = {
  /** DeviceMotion update interval. 16 ms ≈ 60 Hz so the overlay can follow the camera at display rate. */
  sensorIntervalMs: 16,

  /**
   * One-euro filter on the camera forward (and up) vector, used only for
   * drawing. minCutoffHz: lower = smoother when still, more lag.
   * beta: higher = less lag when turning fast. dCutoffHz: derivative smoothing.
   */
  oneEuro: { minCutoffHz: 3.0, beta: 0.8, dCutoffHz: 1.0 },

  /**
   * Heading drift correction time constant (s). Short-term heading is gyro
   * integrated; the magnetometer-corrected attitude heading pulls it back this
   * slowly, so its noise and jumps never reach the dots. Replaces the old 1.5°
   * dead zone, which snapped the dots when it released.
   */
  headingDriftTauSec: 6,

  /** Aim hysteresis: lock on inside enterDeg, lose lock only beyond exitDeg. */
  aimEnterDeg: 3,
  aimExitDeg: 7,

  /** Hold this long while locked and steady before auto-capture fires (the ring fills over this time). */
  holdMs: 1000,

  /** Gyro speed (deg/s, low-passed) below which the phone counts as steady. */
  steadyMaxDegPerSec: 7,
  /** Low-pass time constant for the gyro speed, ms. */
  rateSmoothingMs: 120,
  /** While locked but shaking, the hold ring drains at this multiple of real time instead of resetting. */
  holdDrainRate: 3,

  /** Manual shutter accepts the current dot inside this wider ring (degrees). */
  manualRingDeg: 12,

  /** Shots tilted more than this (degrees up or down) are blocked, auto and manual. */
  tiltLimitDeg: 18,
  /** Horizon cue turns green inside ± this many degrees of roll and pitch. */
  levelOkDeg: 8,

  /** No auto or manual shot, and no next-dot highlight, for this long after a shot (covers the flash). */
  shotCooldownMs: 800,
  /** The next dot is suggested only after the camera has turned this far from the last shot. */
  nextDotMinMoveDeg: 10,
  /** Log raw alpha, fused yaw, target angle and lock state (dev builds only). */
  debugAimLog: false,

  /** Lock-on haptic plays at most once per this many ms. */
  hapticCooldownMs: 600,
} as const;

export type CaptureTuning = typeof CAPTURE_TUNING;

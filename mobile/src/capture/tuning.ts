// Every capture tunable in one place. Values are first guesses: tune on a
// real iPhone (TestFlight) and keep the comments in sync with what you change.

export const CAPTURE_TUNING = {
  /** DeviceMotion update interval. 16 ms ≈ 60 Hz; 33 ms ≈ 30 Hz saves battery. */
  sensorIntervalMs: 16,

  /**
   * One-euro filter on the camera forward (and up) vector, used only for
   * drawing. minCutoffHz: lower = smoother when still, more lag.
   * beta: higher = less lag when turning fast. dCutoffHz: derivative smoothing.
   */
  oneEuro: { minCutoffHz: 1.0, beta: 0.4, dCutoffHz: 1.0 },

  /** Movements smaller than this (degrees) do not move the dots at all. */
  deadZoneDeg: 1.5,

  /** Aim hysteresis: lock on inside enterDeg, lose lock only beyond exitDeg. */
  aimEnterDeg: 4,
  aimExitDeg: 7,

  /** Hold this long while locked and steady before auto-capture fires. */
  holdMs: 400,

  /** Gyro speed (deg/s, low-passed) below which the phone counts as steady. */
  steadyMaxDegPerSec: 12,
  /** Low-pass time constant for the gyro speed, ms. */
  rateSmoothingMs: 120,
  /** While locked but shaking, the hold ring drains at this multiple of real time instead of resetting. */
  holdDrainRate: 1.5,

  /** Manual shutter accepts the current dot inside this wider ring (degrees). */
  manualRingDeg: 12,

  /** Shots tilted more than this (degrees up or down) are blocked, auto and manual. */
  tiltLimitDeg: 18,
  /** Horizon cue turns green inside ± this many degrees of roll and pitch. */
  levelOkDeg: 8,
} as const;

export type CaptureTuning = typeof CAPTURE_TUNING;

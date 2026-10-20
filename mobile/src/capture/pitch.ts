// Pitch (degrees, + = up) recorded per shot and sent to the stitcher.
// An invalid reading (NaN, infinite, beyond ±90°) is never coerced to a number:
// the whole pitch list is dropped and the server treats pitch as not sent,
// exactly like a build-7 upload without pitch.

export const PITCH_LIMIT_DEG = 90;

export function validPitch(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= PITCH_LIMIT_DEG ? value : null;
}

/** Pitches to upload, or undefined (omit the field) if any shot lacks a valid one. */
export function pitchesForUpload(values: readonly unknown[] | null | undefined): number[] | undefined {
  if (!values || values.length === 0) return undefined;
  const out: number[] = [];
  for (const v of values) {
    const p = validPitch(v);
    if (p === null) return undefined;
    out.push(p);
  }
  return out;
}

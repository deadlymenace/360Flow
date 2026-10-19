// Low-light / blur hint from the EXIF that takePictureAsync({ exif: true }) returns.
// Pure TypeScript (no native imports) so it runs under `npm test`.

// NEEDS TUNING ON A DEVICE: these are first guesses from the UX review (item 16).
// Phones differ a lot in how long they expose and how far they push ISO.
export const LOW_LIGHT_THRESHOLDS = {
  /** Seconds. Longer than about 1/30 s handheld tends to blur. */
  maxExposureSeconds: 1 / 30,
  /** ISO above this is usually a dark room and a noisy, soft photo. */
  maxIso: 1600,
} as const;

export const LOW_LIGHT_HINT = 'Low light, turn on more lights or hold steadier. Tap Retake last to redo it.';

type Exif = Record<string, unknown> | null | undefined;

function num(value: unknown): number | null {
  if (Array.isArray(value)) return num(value[0]);
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const fraction = value.match(/^\s*(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)\s*$/);
    if (fraction) {
      const den = Number(fraction[2]);
      return den > 0 ? Number(fraction[1]) / den : null;
    }
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** iOS and Android name the keys differently; take the first one present. */
function pick(exif: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = num(exif[key]);
    if (value != null) return value;
  }
  return null;
}

export function isLowLight(exif: Exif, thresholds = LOW_LIGHT_THRESHOLDS): boolean {
  if (!exif || typeof exif !== 'object') return false;
  const exposure = pick(exif, ['ExposureTime', 'exposureTime']);
  const iso = pick(exif, ['ISOSpeedRatings', 'ISO', 'PhotographicSensitivity', 'ISOSpeed']);
  if (exposure != null && exposure > thresholds.maxExposureSeconds) return true;
  if (iso != null && iso > thresholds.maxIso) return true;
  return false;
}

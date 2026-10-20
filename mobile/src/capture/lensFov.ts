// Field of view of the lens that actually took a photo, from its EXIF.
// The app picks a lens by name, but Expo Go and some phones fall back to a
// different one; the dots and the stitcher must use the real FOV.

function num(v: unknown): number | null {
  const n = typeof v === 'string' ? Number(v) : typeof v === 'number' ? v : NaN;
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * Horizontal FOV (degrees) of the photo as held in portrait, from
 * FocalLenIn35mmFilm (diagonal of a 36×24 mm frame = 43.27 mm). The short
 * side is the portrait width, so aspect only decides the short/diagonal ratio.
 */
export function hfovFromExif(exif: Record<string, unknown> | null | undefined): number | null {
  if (!exif || typeof exif !== 'object') return null;
  const f35 = num(exif.FocalLenIn35mmFilm ?? exif.FocalLengthIn35mmFilm ?? exif.FocalLengthIn35mmFormat);
  if (!f35) return null;
  const w = num(exif.PixelXDimension ?? exif.ImageWidth) ?? 4;
  const h = num(exif.PixelYDimension ?? exif.ImageLength) ?? 3;
  const short = Math.min(w, h);
  const diag = Math.hypot(w, h);
  const diagFov = 2 * Math.atan(43.27 / (2 * f35));
  const hfov = 2 * Math.atan((short / diag) * Math.tan(diagFov / 2));
  const deg = (hfov * 180) / Math.PI;
  return deg > 20 && deg < 150 ? Math.round(deg * 10) / 10 : null;
}

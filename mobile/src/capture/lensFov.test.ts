import { test } from 'node:test';
import assert from 'node:assert/strict';
import { hfovFromExif } from './lensFov';

test('ultra-wide 13 mm portrait is ~90°, wide 26 mm ~53°', () => {
  const uw = hfovFromExif({ FocalLenIn35mmFilm: 13, PixelXDimension: 4032, PixelYDimension: 3024 });
  const wide = hfovFromExif({ FocalLenIn35mmFilm: 26, PixelXDimension: 4032, PixelYDimension: 3024 });
  assert.ok(uw && Math.abs(uw - 90) < 2, String(uw));
  assert.ok(wide && Math.abs(wide - 53) < 2, String(wide));
  assert.equal(hfovFromExif({}), null);
});

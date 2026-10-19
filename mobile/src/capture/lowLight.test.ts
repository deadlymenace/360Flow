import { test } from 'node:test';
import assert from 'node:assert/strict';
import { LOW_LIGHT_THRESHOLDS, isLowLight } from './lowLight';

test('bright, fast shot is fine', () => {
  assert.equal(isLowLight({ ExposureTime: 1 / 120, ISOSpeedRatings: [100] }), false);
});

test('long exposure warns', () => {
  assert.equal(isLowLight({ ExposureTime: 1 / 15, ISOSpeedRatings: [200] }), true);
});

test('high ISO warns (iOS array and Android string forms)', () => {
  assert.equal(isLowLight({ ISOSpeedRatings: [3200] }), true);
  assert.equal(isLowLight({ ISOSpeedRatings: '2000', ExposureTime: '0.01' }), true);
});

test('fraction strings parse', () => {
  assert.equal(isLowLight({ ExposureTime: '1/10' }), true);
  assert.equal(isLowLight({ ExposureTime: '1/250' }), false);
});

test('missing or junk EXIF never warns', () => {
  assert.equal(isLowLight(undefined), false);
  assert.equal(isLowLight(null), false);
  assert.equal(isLowLight({ ExposureTime: 'n/a' }), false);
});

test('threshold edges do not warn', () => {
  assert.equal(
    isLowLight({ ExposureTime: LOW_LIGHT_THRESHOLDS.maxExposureSeconds, ISOSpeedRatings: [LOW_LIGHT_THRESHOLDS.maxIso] }),
    false,
  );
});

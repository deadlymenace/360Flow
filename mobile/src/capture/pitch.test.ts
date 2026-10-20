import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pitchesForUpload, validPitch } from './pitch.js';

test('invalid pitch is rejected, never coerced', () => {
  assert.equal(validPitch(NaN), null);
  assert.equal(validPitch(Infinity), null);
  assert.equal(validPitch(-Infinity), null);
  assert.equal(validPitch(91), null);
  assert.equal(validPitch(undefined), null);
  assert.equal(validPitch(null), null);
  assert.equal(validPitch(-12.5), -12.5);
  assert.equal(validPitch(90), 90);
});

test('one bad or missing pitch drops the list (sent as "no pitch")', () => {
  assert.deepEqual(pitchesForUpload([1, -2, 3]), [1, -2, 3]);
  assert.equal(pitchesForUpload([1, NaN, 3]), undefined);
  assert.equal(pitchesForUpload([1, null, 3]), undefined);
  assert.equal(pitchesForUpload([]), undefined);
  assert.equal(pitchesForUpload(undefined), undefined);
  const body = JSON.parse(JSON.stringify({ hfov_deg: 60, pitches_deg: pitchesForUpload([0, NaN]) }));
  assert.equal('pitches_deg' in body, false);
});

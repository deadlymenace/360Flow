// Run with `npm test` (compiles with tsc, then node --test). No native imports.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  TimeoutError,
  UploadError,
  classifyError,
  errorForStatus,
  friendlyUploadMessage,
  nextStepAfterFailure,
  nextStepAfterStatusCheck,
} from './uploadPolicy';

test('prepare-stage network failure auto-retries once (request never left the phone)', () => {
  const err = classifyError(new TypeError('Network request failed'), 'prepare');
  assert.equal(nextStepAfterFailure(err, 1).action, 'retry-upload');
  assert.equal(nextStepAfterFailure(err, 2).action, 'show-error');
});

test('timeout during send never re-uploads blindly: it checks status first', () => {
  const err = classifyError(new TimeoutError(), 'send');
  assert.equal(err.kind, 'timeout');
  assert.equal(nextStepAfterFailure(err, 1).action, 'check-status');
});

test('lost response during send checks status first', () => {
  const err = classifyError(new TypeError('Network request failed'), 'send');
  assert.equal(nextStepAfterFailure(err, 1).action, 'check-status');
});

test('status check: upload landed -> poll, never re-upload (avoids a second paid run)', () => {
  for (const status of ['uploading', 'stitching', 'stitched', 'generating_3d', 'complete', 'failed', 'delayed']) {
    assert.equal(nextStepAfterStatusCheck(status, 1).action, 'poll', status);
  }
});

test('status check: still waiting for photos -> re-upload once', () => {
  assert.equal(nextStepAfterStatusCheck('created', 1).action, 'retry-upload');
  assert.equal(nextStepAfterStatusCheck('created', 2).action, 'show-error');
});

test('status check itself failed -> show error, no auto re-upload', () => {
  assert.equal(nextStepAfterStatusCheck(null, 1).action, 'show-error');
});

test('HTTP errors from the server are not auto-retried', () => {
  assert.equal(nextStepAfterFailure(errorForStatus(500), 1).action, 'show-error');
  assert.equal(nextStepAfterFailure(errorForStatus(413), 1).action, 'show-error');
});

test('friendly copy hides raw errors', () => {
  const msg = friendlyUploadMessage(new UploadError('server', 'send', 'HTTP 502 <html>', 502));
  assert.ok(!msg.includes('502'));
  assert.match(friendlyUploadMessage(new UploadError('offline', 'prepare', 'x')), /No connection/);
});

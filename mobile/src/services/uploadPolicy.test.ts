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

test('HTTP errors are never blindly re-sent: 5xx checks status, 4xx shows an error', () => {
  assert.equal(nextStepAfterFailure(errorForStatus(500), 1).action, 'check-status');
  assert.equal(nextStepAfterFailure(errorForStatus(413), 1).action, 'show-error');
});

test('friendly copy hides raw errors', () => {
  const msg = friendlyUploadMessage(new UploadError('server', 'send', 'HTTP 502 <html>', 502));
  assert.ok(!msg.includes('502'));
  assert.match(friendlyUploadMessage(new UploadError('offline', 'prepare', 'x')), /No connection/);
});

// ---- Session tests (fake I/O) ----
import { newUploadSession, runUploadSession, outcomeUnknown, UPLOAD_TIMEOUT_MS } from './uploadPolicy';

function fakeDeps(opts: { uploads: Array<() => void>; status: string | (() => string) }) {
  const calls = { upload: 0, status: 0 };
  return {
    calls,
    deps: {
      upload: async () => {
        const step = opts.uploads[calls.upload++];
        if (!step) throw new Error('unexpected upload');
        step();
      },
      getStatus: async () => {
        calls.status++;
        return typeof opts.status === 'function' ? opts.status() : opts.status;
      },
      sleep: async () => {},
    },
  };
}
const fail502 = () => {
  throw errorForStatus(502, 'send');
};

test('5xx at send is an unknown outcome', () => {
  assert.equal(outcomeUnknown(errorForStatus(502, 'send')), true);
  assert.equal(outcomeUnknown(errorForStatus(504, 'send')), true);
  assert.equal(nextStepAfterFailure(errorForStatus(502, 'send'), 1).action, 'check-status');
});

test('502 at send, then manual Retry with server reporting stitching -> Processing, no upload', async () => {
  const session = newUploadSession();
  // First attempt: 502, and the status check fails (still unknown) -> error shown.
  const first = fakeDeps({ uploads: [fail502], status: () => { throw new TypeError('Network request failed'); } });
  const r1 = await runUploadSession(session, first.deps);
  assert.equal(r1.result, 'error');
  assert.equal(session.reachedSend, true);
  // Manual Retry: server now says stitching.
  const retry = fakeDeps({ uploads: [], status: 'stitching' });
  const r2 = await runUploadSession(session, retry.deps);
  assert.equal(r2.result, 'poll');
  assert.equal(retry.calls.upload, 0);
  assert.equal(retry.calls.status, 1);
});

test('502 at send, then manual Retry with server reporting created -> re-uploads', async () => {
  const session = newUploadSession();
  const first = fakeDeps({ uploads: [fail502], status: () => { throw new TypeError('Network request failed'); } });
  assert.equal((await runUploadSession(session, first.deps)).result, 'error');
  const retry = fakeDeps({ uploads: [() => {}], status: 'created' });
  const r2 = await runUploadSession(session, retry.deps);
  assert.equal(r2.result, 'poll');
  assert.equal(retry.calls.status, 1);
  assert.equal(retry.calls.upload, 1);
});

test('502 at send with status stitching on the immediate check -> poll, one upload only', async () => {
  const session = newUploadSession();
  const d = fakeDeps({ uploads: [fail502], status: 'stitching' });
  assert.equal((await runUploadSession(session, d.deps)).result, 'poll');
  assert.equal(d.calls.upload, 1);
});

test('409 on a retry upload means already processing -> poll, not error', async () => {
  const session = newUploadSession();
  // First: timeout at send; status says created, so it re-uploads; that gets 409.
  const d = fakeDeps({
    uploads: [
      () => { throw new UploadError('timeout', 'send', 'Request timed out'); },
      () => { throw errorForStatus(409, 'send'); },
    ],
    status: 'created',
  });
  const r = await runUploadSession(session, d.deps);
  assert.equal(r.result, 'poll');
  assert.equal(d.calls.upload, 2);
  assert.equal(nextStepAfterFailure(errorForStatus(409, 'send'), 2).action, 'poll');
});

test('upload timeout constant is 120 s', () => {
  assert.equal(UPLOAD_TIMEOUT_MS, 120_000);
});

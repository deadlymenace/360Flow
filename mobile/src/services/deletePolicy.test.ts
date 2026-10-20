import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DeleteProjectError,
  SingleFlight,
  deleteWithTimeout,
  hideRowAfter,
  friendlyDeleteMessage,
  isLocalDraft,
  markDeleted,
  outcomeForDeleteStatus,
  withoutDeleted,
} from './deletePolicy';

test('local-only draft is recognised (removed without an API call)', () => {
  assert.equal(isLocalDraft('local-1729412345'), true);
  assert.equal(isLocalDraft('c4dbe508-83a3-4bf8-a3db-d7403fb9fb68'), false);
});

test('204 deletes, 404 counts as already gone', () => {
  assert.equal(outcomeForDeleteStatus(204), 'deleted');
  assert.equal(outcomeForDeleteStatus(404), 'already-gone');
});

test('other errors throw friendly copy, never the raw status text', () => {
  for (const status of [500, 401, 429, 400]) {
    assert.throws(
      () => outcomeForDeleteStatus(status),
      (e: unknown) => e instanceof DeleteProjectError && !/failed: \d+/.test(e.message),
    );
  }
  assert.match(friendlyDeleteMessage(null), /connection/);
});

test('a room deleted from Capture is hidden from a stale list (the Balcony 404)', () => {
  const stale = [{ id: 'balcony' }, { id: 'kitchen' }];
  markDeleted('balcony');
  assert.deepEqual(withoutDeleted(stale), [{ id: 'kitchen' }]);
});

test('deleteWithTimeout: resolves with the outcome when fast', async () => {
  assert.equal(await deleteWithTimeout(async () => 'deleted', 50), 'deleted');
  assert.equal(await deleteWithTimeout(async () => 'already-gone', 50), 'already-gone');
});

test('deleteWithTimeout: times out instead of hanging', async () => {
  const outcome = await deleteWithTimeout(() => new Promise(() => {}), 20);
  assert.equal(outcome, 'timeout');
  assert.equal(hideRowAfter(outcome), true);
});

test('deleteWithTimeout: an error is reported, not thrown, and keeps the row', async () => {
  const outcome = await deleteWithTimeout(async () => { throw new Error('offline'); }, 50);
  assert.equal(outcome, 'failed');
  assert.equal(hideRowAfter(outcome), false);
});

test('SingleFlight: a second delete while one is in flight is ignored', async () => {
  const sf = new SingleFlight();
  let calls = 0;
  let release: () => void = () => {};
  const first = sf.run(() => { calls++; return new Promise<void>((r) => { release = r; }); });
  assert.equal(sf.inFlight, true);
  assert.equal(await sf.run(async () => { calls++; }), undefined);
  release();
  await first;
  assert.equal(calls, 1);
  assert.equal(sf.inFlight, false);
});

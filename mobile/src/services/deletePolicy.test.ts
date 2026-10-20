import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DeleteProjectError,
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

/**
 * Upload error classification and retry policy.
 *
 * BILLING: a successful POST /projects/{id}/upload immediately enqueues
 * stitching, which then starts a paid World Labs run. A second accepted upload
 * would start a second paid run. So:
 *
 *  - We only auto-retry when the request clearly never left the phone
 *    (`stage: 'prepare'`: device registration or copying the photos failed
 *    before `fetch` was called, or the device was already known offline).
 *  - On a timeout, a lost response, or a transport error *during* the POST we
 *    cannot know whether the server received it. We first GET the project's
 *    status and re-upload only if the project is still waiting for photos
 *    (`created`). Any other status means the upload landed (or a job is
 *    running), so we go to polling instead of uploading again.
 *
 * Pure functions only, so `uploadPolicy.test.ts` can run under plain Node.
 */

export type UploadStage = 'prepare' | 'send';

export type UploadErrorKind = 'offline' | 'timeout' | 'server' | 'client' | 'unknown';

export class UploadError extends Error {
  kind: UploadErrorKind;
  stage: UploadStage;
  status?: number;
  constructor(kind: UploadErrorKind, stage: UploadStage, message: string, status?: number) {
    super(message);
    this.name = 'UploadError';
    this.kind = kind;
    this.stage = stage;
    this.status = status;
  }
}

export class TimeoutError extends Error {
  constructor(message = 'Request timed out') {
    super(message);
    this.name = 'TimeoutError';
  }
}

/** RN fetch rejects with `TypeError: Network request failed` when there is no route. */
export function isNetworkError(e: unknown): boolean {
  if (!(e instanceof Error)) return false;
  return e.name === 'TypeError' || /network request failed|network error|offline/i.test(e.message);
}

export function classifyError(e: unknown, stage: UploadStage): UploadError {
  if (e instanceof UploadError) return e;
  if (e instanceof TimeoutError || (e instanceof Error && e.name === 'TimeoutError')) {
    return new UploadError('timeout', stage, (e as Error).message);
  }
  if (isNetworkError(e)) return new UploadError('offline', stage, (e as Error).message);
  return new UploadError('unknown', stage, e instanceof Error ? e.message : String(e));
}

export function errorForStatus(status: number, stage: UploadStage = 'send'): UploadError {
  if (status >= 500) return new UploadError('server', stage, `HTTP ${status}`, status);
  return new UploadError('client', stage, `HTTP ${status}`, status);
}

/** Human copy. Never shows raw HTTP bodies or backend jargon. */
export function friendlyUploadMessage(err: UploadError): string {
  switch (err.kind) {
    case 'offline':
      return "No connection. Your photos are safe on this phone. Retry when you're back online.";
    case 'timeout':
      return 'Upload is slow. Try Wi-Fi, then Retry. Your photos are safe on this phone.';
    case 'server':
      return 'Server problem. Your photos are safe on this phone. Retry in a moment.';
    case 'client':
      if (err.status === 413) return 'These photos are too large to upload. Try capturing again.';
      if (err.status === 409) return 'This capture is already processing.';
      if (err.status === 404) return 'This capture no longer exists. Start a new capture from Home.';
      return 'The upload was rejected. Retry, or start a new capture.';
    default:
      return 'Upload failed. Your photos are safe on this phone. Retry.';
  }
}

/** True when the request certainly never reached the server, so a resend cannot double-bill. */
export function clearlyNeverSent(err: UploadError): boolean {
  return err.stage === 'prepare';
}

/** True when we cannot tell whether the server accepted the upload. */
export function outcomeUnknown(err: UploadError): boolean {
  return err.stage === 'send' && (err.kind === 'timeout' || err.kind === 'offline' || err.kind === 'unknown');
}

/** Project statuses that mean the server is still waiting for photos. */
export function isWaitingForPhotos(status: string): boolean {
  return status === 'created';
}

export type NextStep =
  | { action: 'retry-upload'; delayMs: number }
  | { action: 'check-status' }
  | { action: 'show-error' };

/**
 * What to do after a failed upload attempt.
 * `attempt` is 1 for the first try. We auto-retry at most once.
 */
export function nextStepAfterFailure(err: UploadError, attempt: number): NextStep {
  if (outcomeUnknown(err)) return { action: 'check-status' };
  if (clearlyNeverSent(err) && attempt < 2 && err.kind !== 'client') {
    return { action: 'retry-upload', delayMs: backoffMs(attempt) };
  }
  return { action: 'show-error' };
}

/**
 * After an ambiguous failure we GET the status.
 * `status` is null when that GET also failed (still offline).
 */
export function nextStepAfterStatusCheck(
  status: string | null,
  attempt: number,
): { action: 'poll' } | { action: 'retry-upload'; delayMs: number } | { action: 'show-error' } {
  if (status == null) return { action: 'show-error' };
  if (!isWaitingForPhotos(status)) return { action: 'poll' };
  // The server never stored the photos, so resending cannot start a second paid run.
  return attempt < 2 ? { action: 'retry-upload', delayMs: backoffMs(attempt) } : { action: 'show-error' };
}

export function backoffMs(attempt: number): number {
  return Math.min(8000, 1500 * 2 ** (attempt - 1));
}

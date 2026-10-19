/**
 * Upload error classification and retry policy.
 *
 * BILLING: a successful POST /projects/{id}/upload immediately enqueues
 * stitching, which then starts a paid World Labs run. A second accepted upload
 * would start a second paid run. So:
 *
 *  - We only auto-retry when the request clearly never left the phone
 *    (`stage: 'prepare'`: device registration or copying the photos failed
 *    before `fetch` was called).
 *  - Any failure *during* the POST whose outcome we cannot know (timeout after
 *    UPLOAD_TIMEOUT_MS, lost response, transport error, or a 5xx such as a
 *    502/504 from a proxy that may have forwarded the body) is "unknown". We
 *    GET the project's status and re-upload only if it is still `created`
 *    (waiting for photos). Any other status means go to polling/Processing.
 *  - Once any attempt in an upload session reached the send step, every later
 *    attempt, including a manual Retry, checks status first.
 *  - A 409 means the project is already processing: poll, don't error.
 *
 * Pure logic with injected I/O, so `uploadPolicy.test.ts` runs under plain Node.
 */

/** Upload POST timeout used by api.ts. User copy says "up to 2 minutes". */
export const UPLOAD_TIMEOUT_MS = 120_000;

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
      return "Upload didn't finish within 2 minutes. Try Wi-Fi, then Retry. Your photos are safe on this phone.";
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
  return (
    err.stage === 'send' &&
    (err.kind === 'timeout' || err.kind === 'offline' || err.kind === 'unknown' || err.kind === 'server')
  );
}

/** 409: the server says this project is already processing. */
export function isAlreadyProcessing(err: UploadError): boolean {
  return err.kind === 'client' && err.status === 409;
}

/** Project statuses that mean the server is still waiting for photos. */
export function isWaitingForPhotos(status: string): boolean {
  return status === 'created';
}

export type NextStep =
  | { action: 'retry-upload'; delayMs: number }
  | { action: 'check-status' }
  | { action: 'poll' }
  | { action: 'show-error' };

/**
 * What to do after a failed upload attempt.
 * `attempt` is 1 for the first try. We auto-retry at most once.
 */
export function nextStepAfterFailure(err: UploadError, attempt: number): NextStep {
  if (isAlreadyProcessing(err)) return { action: 'poll' };
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

/** Per-upload-session state. Create one per Processing screen / photo set. */
export type UploadSession = {
  /** True once any attempt got as far as sending the POST. */
  reachedSend: boolean;
};

export function newUploadSession(): UploadSession {
  return { reachedSend: false };
}

export type UploadDeps = {
  /** Performs the upload. Must throw an `UploadError` (or anything classifiable). */
  upload: () => Promise<void>;
  /** Returns the project's status string. Throws on network failure. */
  getStatus: () => Promise<string>;
  sleep: (ms: number) => Promise<void>;
  /** Called when a status check starts, so the UI can show "Checking upload…". */
  onPhase?: (phase: 'checking') => void;
};

export type UploadOutcome = { result: 'poll' } | { result: 'error'; error: UploadError };

async function statusOrNull(deps: UploadDeps): Promise<string | null> {
  deps.onPhase?.('checking');
  try {
    return await deps.getStatus();
  } catch {
    return null;
  }
}

/**
 * Run one upload attempt sequence (first try or a manual Retry) for a session.
 * Never uploads after a send-stage attempt without first seeing `created`.
 */
export async function runUploadSession(session: UploadSession, deps: UploadDeps): Promise<UploadOutcome> {
  let lastError: UploadError | null = null;
  // Manual Retry after an earlier send attempt: check status before anything.
  if (session.reachedSend) {
    const status = await statusOrNull(deps);
    if (status == null) {
      return { result: 'error', error: new UploadError('offline', 'send', 'Status check failed') };
    }
    if (!isWaitingForPhotos(status)) return { result: 'poll' };
  }
  let attempt = 1;
  for (;;) {
    try {
      await deps.upload();
      return { result: 'poll' };
    } catch (e) {
      const err = e instanceof UploadError ? e : classifyError(e, 'send');
      lastError = err;
      if (err.stage === 'send') session.reachedSend = true;
      let step: NextStep | ReturnType<typeof nextStepAfterStatusCheck> = nextStepAfterFailure(err, attempt);
      if (step.action === 'check-status') {
        step = nextStepAfterStatusCheck(await statusOrNull(deps), attempt);
      }
      if (step.action === 'poll') return { result: 'poll' };
      if (step.action === 'retry-upload') {
        attempt += 1;
        await deps.sleep(step.delayMs);
        continue;
      }
      return { result: 'error', error: lastError };
    }
  }
}

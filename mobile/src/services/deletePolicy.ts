// What a project delete means for the list. Pure TypeScript for `npm test`.

/** Drafts that never reached the server carry this id prefix. */
export const LOCAL_DRAFT_PREFIX = 'local-';

export function isLocalDraft(projectId: string): boolean {
  return projectId.startsWith(LOCAL_DRAFT_PREFIX);
}

export type DeleteOutcome = 'deleted' | 'already-gone';

export class DeleteProjectError extends Error {
  constructor(public status: number | null, message: string) {
    super(message);
    this.name = 'DeleteProjectError';
  }
}

/**
 * 2xx: deleted. 404: already gone (another screen, a stale list, or another
 * request deleted it first), so the caller just drops it from the list.
 * Anything else is a real failure with friendly copy.
 */
export function outcomeForDeleteStatus(status: number): DeleteOutcome {
  if (status >= 200 && status < 300) return 'deleted';
  if (status === 404) return 'already-gone';
  throw new DeleteProjectError(status, friendlyDeleteMessage(status));
}

export function friendlyDeleteMessage(status: number | null): string {
  if (status === 401 || status === 403) return "This phone can't delete that room. Pull to refresh and try again.";
  if (status === 429) return 'Too many requests. Wait a few seconds and try again.';
  if (status != null && status >= 500) return "360Flow couldn't delete it right now. Try again in a minute.";
  if (status == null) return "Couldn't reach 360Flow. Check your connection and try again.";
  return "Couldn't delete this room. Try again.";
}

// Projects deleted from another screen (for example "Discard and delete room"
// on Capture, which does not wait for the server). Home hides them so a list
// fetched before the DELETE landed cannot show a ghost row.
const recentlyDeleted = new Set<string>();

export function markDeleted(projectId: string): void {
  recentlyDeleted.add(projectId);
}

export function withoutDeleted<T extends { id: string }>(projects: T[]): T[] {
  return projects.filter((project) => !recentlyDeleted.has(project.id));
}

export type TimedDeleteOutcome = DeleteOutcome | 'timeout' | 'failed';

/** "Discard and delete room" waits at most this long before going back anyway. */
export const DISCARD_DELETE_TIMEOUT_MS = 8000;

/**
 * Run a delete, but never wait longer than timeoutMs. Never throws:
 * a timeout or an error is reported so the caller can still navigate.
 * The request itself keeps running after a timeout and may still land.
 */
export function deleteWithTimeout(
  run: () => Promise<DeleteOutcome>,
  timeoutMs: number = DISCARD_DELETE_TIMEOUT_MS,
): Promise<TimedDeleteOutcome> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (outcome: TimedDeleteOutcome) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(outcome);
    };
    const timer = setTimeout(() => finish('timeout'), timeoutMs);
    run().then(finish, () => finish('failed'));
  });
}

/** Row should disappear from Home: deleted, already gone, or still in flight after the timeout. */
export function hideRowAfter(outcome: TimedDeleteOutcome): boolean {
  return outcome !== 'failed';
}

/** Runs a task at most once at a time; a second call while one is in flight is ignored. */
export class SingleFlight {
  private busy = false;
  get inFlight(): boolean {
    return this.busy;
  }
  async run<T>(task: () => Promise<T>): Promise<T | undefined> {
    if (this.busy) return undefined;
    this.busy = true;
    try {
      return await task();
    } finally {
      this.busy = false;
    }
  }
}

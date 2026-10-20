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

export const DISCLOSURE_TEXT = 'Virtually staged (AI)';

export interface Project {
  id: string;
  name: string;
  status: ProjectStatus;
  created_at: string;
  image_count: number;
  panorama_path: string | null;
  error: string | null;
  staging_prompt: string | null;
  share_token: string;
  share_url: string | null;
  preview_url: string | null;
  has_original: boolean;
  has_staged: boolean;
  staged_published: boolean;
  staging_provider: string | null;
  world_provider: string | null;
  world_viewer_url: string | null;
  /** Full 3D world finished. Provider URLs are never sent to the app. */
  world_ready: boolean;
  /** Fast draft world ready while the full one generates. */
  world_draft_ready: boolean;
  staged_world_ready: boolean;
  staged_world_draft_ready: boolean;
  staged_world_viewer_url: string | null;
  staging_notes: string | null;
  staging_status: 'staging' | 'staged' | 'failed' | null;
  staging_error: string | null;
  active_job: string | null;
  disclosure: string | null;
}

export type ProjectStatus =
  | 'created'
  | 'uploading'
  | 'stitching'
  | 'stitched'
  | 'staging'
  | 'staged'
  | 'generating_3d'
  | 'delayed'
  | 'complete'
  | 'failed';

export interface StatusResponse {
  status: ProjectStatus;
  progress: number;
  error: string | null;
  share_url?: string | null;
  has_original?: boolean;
  has_staged?: boolean;
  staging_provider?: string | null;
  staging_status?: 'staging' | 'staged' | 'failed' | null;
  staging_error?: string | null;
  world_provider?: string | null;
  world_ready?: boolean;
  world_draft_ready?: boolean;
  active_job?: string | null;
  disclosure?: string | null;
}

export interface StageStatus {
  status: ProjectStatus;
  prompt: string | null;
  provider: string;
  has_staged: boolean;
  disclosure: string | null;
  notes: string | null;
  error: string | null;
}

export interface WorldAssets {
  viewer_url: string;
  ready: boolean;
  draft_ready: boolean;
}

export interface WorldStatus {
  status: ProjectStatus;
  provider: string;
  error: string | null;
  disclosure: string | null;
  assets: WorldAssets | null;
  staged_assets: WorldAssets | null;
}

export interface CaptureSegment {
  id: number;
  targetYaw: number;
  imageUri: string | null;
  actualYaw: number | null;
  /** Camera pitch at the shutter, degrees, positive = up. */
  actualPitch?: number | null;
  capturedAt: Date | null;
}

/** Single source of truth for wait-time copy. Drafts take ~30 s; full worlds 7–10 min. */
export const DRAFT_ETA_S = 30;
export const FULL_ETA_MIN = '7–10';
/** Upper bound of FULL_ETA_MIN in minutes, used for "~N min left" estimates. */
export const FULL_ETA_MAX_MIN = 10;
/** Keep polling for the full world at least this long before saying it is slow. */
export const WORLD_GENERATION_WAIT_MS = 12 * 60 * 1000;
/** Stitching is quick. After this long with no panorama, offer Retry. */
export const STITCH_SLOW_MS = 3 * 60 * 1000;

export const SEGMENT_COUNT = 16;
export const SEGMENT_ANGLE = 360 / SEGMENT_COUNT; // 22.5°

/** Portrait ultra-wide (iPhone 11+, ~120° diagonal) horizontal field of view. */
export const ULTRA_WIDE_PORTRAIT_HFOV = 92;
/** Portrait main wide camera, used only if the ultra-wide lens is missing. */
export const WIDE_PORTRAIT_HFOV = 53;

export interface StagingPreset {
  id: string;
  label: string;
  prompt: string;
}

export const STAGING_PRESETS: StagingPreset[] = [
  {
    id: 'modern',
    label: 'Modern',
    prompt: 'modern low sofa, light wood coffee table, linen cushions, one framed print, and a small plant',
  },
  {
    id: 'scandinavian',
    label: 'Scandinavian',
    prompt: 'pale oak sofa, wool throw, simple oak coffee table, ceramic lamp, and a leafy plant',
  },
  {
    id: 'industrial',
    label: 'Industrial',
    prompt: 'leather sofa, dark metal coffee table, factory-style floor lamp, and a woven rug',
  },
  {
    id: 'traditional',
    label: 'Traditional',
    prompt: 'rolled-arm sofa, wood coffee table, table lamp, framed art, and a classic area rug',
  },
  {
    id: 'luxury',
    label: 'Luxury',
    prompt: 'tailored sofa, marble-top coffee table, brass floor lamp, and a plush area rug',
  },
  {
    id: 'minimal',
    label: 'Minimal',
    prompt: 'low platform sofa, one slim side table, a single floor lamp, and a small plant',
  },
];

export function makeSegments(): CaptureSegment[] {
  return Array.from({ length: SEGMENT_COUNT }, (_, i) => ({
    id: i,
    targetYaw: i * SEGMENT_ANGLE,
    imageUri: null,
    actualYaw: null,
    actualPitch: null,
    capturedAt: null,
  }));
}

export function getStatusLabel(status: ProjectStatus): string {
  switch (status) {
    case 'created': return 'Not captured yet';
    case 'uploading': return 'Uploading...';
    case 'stitching': return 'Stitching...';
    case 'stitched': return 'Panorama ready';
    case 'staging': return 'Staging...';
    case 'staged': return 'Staged';
    case 'generating_3d': return 'Building 3D…';
    case 'delayed': return '3D delayed';
    case 'complete': return '3D tour ready';
    case 'failed': return 'Failed';
  }
}

export function getStatusColor(status: ProjectStatus): string {
  switch (status) {
    case 'complete':
    case 'stitched':
    case 'staged':
      return '#4CAF50';
    case 'failed':
      return '#F44336';
    default:
      return '#FF9800';
  }
}

/**
 * Live, human status for a Home row. Uses only fields the backend already returns.
 */
export function liveStatusLabel(project: Project): string {
  const buildingWorld =
    project.status === 'generating_3d' ||
    project.active_job === 'world' ||
    project.active_job === 'staged_world';
  if (project.status === 'created') return 'Not captured yet · Tap to capture';
  if (project.status === 'delayed') return '3D delayed, finishing shortly';
  if (buildingWorld) {
    if (project.world_draft_ready) return '3D preview ready';
    return `Full tour in ~${FULL_ETA_MIN} min`;
  }
  if (project.staging_status === 'staging') return 'Staging…';
  return getStatusLabel(project.status);
}

export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return '';
  const s = Math.max(0, Math.round((now - then) / 1000));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d} d ago`;
  return new Date(iso).toLocaleDateString();
}

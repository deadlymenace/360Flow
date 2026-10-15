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
  world_marble_url: string | null;
  world_thumbnail_url: string | null;
  world_splat_url: string | null;
  world_mesh_url: string | null;
  world_preview_url: string | null;
  staged_world_marble_url: string | null;
  staged_world_preview_url: string | null;
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
  world_preview_url?: string | null;
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
  marble_url: string | null;
  preview_url: string | null;
  splat_url: string | null;
  mesh_url: string | null;
  thumbnail_url: string | null;
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
  capturedAt: Date | null;
}

/** Full Marble worlds have taken about 9 minutes. Keep polling at least this long. */
export const WORLD_GENERATION_WAIT_MS = 15 * 60 * 1000;

export const SEGMENT_COUNT = 16;
export const SEGMENT_ANGLE = 360 / SEGMENT_COUNT; // 22.5°
export const ALIGNMENT_TOLERANCE = 5; // degrees

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
    capturedAt: null,
  }));
}

export function getStatusLabel(status: ProjectStatus): string {
  switch (status) {
    case 'created': return 'New';
    case 'uploading': return 'Uploading...';
    case 'stitching': return 'Stitching...';
    case 'stitched': return 'Panorama Ready';
    case 'staging': return 'Staging...';
    case 'staged': return 'Staged';
    case 'generating_3d': return 'Generating 3D...';
    case 'delayed': return '3D delayed';
    case 'complete': return '3D Ready';
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

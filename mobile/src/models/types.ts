export interface Project {
  id: string;
  name: string;
  status: ProjectStatus;
  created_at: string;
  image_count: number;
  panorama_path: string | null;
  error: string | null;
  staging_prompt: string | null;
}

export type ProjectStatus =
  | 'created'
  | 'uploading'
  | 'stitching'
  | 'stitched'
  | 'staging'
  | 'staged'
  | 'generating_3d'
  | 'complete'
  | 'failed';

export interface StatusResponse {
  status: ProjectStatus;
  progress: number;
  error: string | null;
}

export interface CaptureSegment {
  id: number;
  targetYaw: number;
  imageUri: string | null;
  actualYaw: number | null;
  capturedAt: Date | null;
}

export const SEGMENT_COUNT = 16;
export const SEGMENT_ANGLE = 360 / SEGMENT_COUNT; // 22.5°
export const ALIGNMENT_TOLERANCE = 5; // degrees

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
    case 'complete': return 'Complete';
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

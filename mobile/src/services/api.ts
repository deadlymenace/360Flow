import { File as ExpoFile, Paths } from 'expo-file-system';
import Constants from 'expo-constants';
import * as SecureStore from 'expo-secure-store';
import { Project, StageStatus, StatusResponse, WorldStatus } from '../models/types';

const DEVICE_TOKEN_KEY = 'device_token';

function configuredBaseUrl(): string | null {
  const configured = process.env.EXPO_PUBLIC_API_URL?.trim();
  if (!configured) return null;
  const trimmed = configured.replace(/\/$/, '');
  return trimmed.endsWith('/api/v1') ? trimmed : `${trimmed}/api/v1`;
}

// EXPO_PUBLIC_API_URL wins so an installed build can reach Railway.
// A dev build may fall back to the Expo debugger host. Release builds have
// no HTTP fallback: the URL must be set and must be https.
export function getBaseUrl(): string {
  const configured = configuredBaseUrl();
  if (configured) {
    if (!__DEV__ && !configured.toLowerCase().startsWith('https://')) {
      throw new Error('EXPO_PUBLIC_API_URL must use https in a release build');
    }
    return configured;
  }
  if (!__DEV__) {
    throw new Error('EXPO_PUBLIC_API_URL must be set for a release build');
  }

  const extra = Constants.manifest2?.extra as { expoGo?: { debuggerHost?: string } } | undefined;
  const debuggerHost = Constants.expoConfig?.hostUri ?? extra?.expoGo?.debuggerHost;
  if (debuggerHost) {
    const host = debuggerHost.split(':')[0];
    return `http://${host}:8000/api/v1`;
  }
  return 'http://localhost:8000/api/v1';
}

export function getApiOrigin(): string {
  return getBaseUrl().replace(/\/api\/v1\/?$/, '');
}

async function deviceToken(): Promise<string> {
  const existing = await SecureStore.getItemAsync(DEVICE_TOKEN_KEY);
  if (existing) return existing;
  const res = await fetch(`${getBaseUrl()}/devices`, { method: 'POST' });
  if (!res.ok) throw new Error('Could not register this device');
  const body = await res.json();
  const token = typeof body.token === 'string' ? body.token : '';
  if (!token) throw new Error('Could not register this device');
  await SecureStore.setItemAsync(DEVICE_TOKEN_KEY, token);
  return token;
}

async function authHeaders(extra: Record<string, string> = {}): Promise<Record<string, string>> {
  const token = await deviceToken();
  return { ...extra, Authorization: `Bearer ${token}` };
}

const REQUEST_TIMEOUT = 10000;

async function fetchWithTimeout(url: string, options: RequestInit = {}, timeout = REQUEST_TIMEOUT): Promise<Response> {
  const headers = await authHeaders(options.headers as Record<string, string> | undefined);
  return Promise.race([
    fetch(url, { ...options, headers }),
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error('Request timed out — is the backend running?')), timeout)
    ),
  ]);
}

export interface UploadOptions {
  yaws?: number[];
  hfov?: number;
  lens?: string;
  onProgress?: (progress: number) => void;
}

class APIService {
  private baseUrl: string;

  constructor(baseUrl: string = getBaseUrl()) {
    this.baseUrl = baseUrl;
  }

  async createProject(name: string): Promise<Project> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) throw new Error(`Create project failed: ${res.status}`);
    return res.json();
  }

  async listProjects(): Promise<Project[]> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects`);
    if (!res.ok) throw new Error(`List projects failed: ${res.status}`);
    return res.json();
  }

  async getProject(projectId: string): Promise<Project> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}`);
    if (!res.ok) throw new Error(`Get project failed: ${res.status}`);
    return res.json();
  }

  async deleteProject(projectId: string): Promise<void> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`Delete project failed: ${res.status}`);
  }

  async uploadImages(projectId: string, imageUris: string[], options: UploadOptions = {}): Promise<void> {
    const formData = new FormData();
    formData.append(
      'metadata',
      JSON.stringify({
        hfov_deg: options.hfov,
        yaws_deg: options.yaws,
        lens: options.lens,
      })
    );

    for (let i = 0; i < imageUris.length; i++) {
      const uri = imageUris[i];
      const filename = `segment_${i.toString().padStart(2, '0')}.jpg`;
      formData.append('images', {
        uri,
        name: filename,
        type: 'image/jpeg',
      } as unknown as Blob);
      options.onProgress?.((i + 1) / imageUris.length * 0.5);
    }

    const res = await fetchWithTimeout(
      `${this.baseUrl}/projects/${projectId}/upload`,
      { method: 'POST', body: formData },
      120000
    );
    options.onProgress?.(1);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Upload failed: ${res.status} - ${text}`);
    }
  }

  async getStatus(projectId: string): Promise<StatusResponse> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/status`);
    if (!res.ok) throw new Error(`Get status failed: ${res.status}`);
    return res.json();
  }

  async downloadToCache(url: string, filename: string): Promise<string> {
    const dest = new ExpoFile(Paths.cache, filename);
    const output = await ExpoFile.downloadFileAsync(url, dest, {
      headers: await authHeaders(),
      idempotent: true,
    });
    return output.uri;
  }

  async publishStaged(projectId: string): Promise<Project> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/staged/publish`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`Publish staged view failed: ${res.status}`);
    return res.json();
  }

  async unpublishStaged(projectId: string): Promise<Project> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/staged/unpublish`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`Unpublish staged view failed: ${res.status}`);
    return res.json();
  }

  async startStaging(projectId: string, prompt: string): Promise<void> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/stage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    });
    if (!res.ok) throw new Error(`Start staging failed: ${res.status}`);
  }

  async getStagingStatus(projectId: string): Promise<StageStatus> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/stage/status`);
    if (!res.ok) throw new Error(`Get staging status failed: ${res.status}`);
    return res.json();
  }

  async startGenerate3D(projectId: string, source: 'original' | 'staged' = 'original'): Promise<void> {
    const res = await fetchWithTimeout(
      `${this.baseUrl}/projects/${projectId}/generate-3d?source=${source}`,
      {
        method: 'POST',
      },
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Start 3D generation failed: ${res.status} ${text}`);
    }
  }

  async retry(projectId: string): Promise<void> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/retry`, {
      method: 'POST',
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Retry failed: ${res.status} ${text}`);
    }
  }

  async getGenerate3DStatus(projectId: string): Promise<WorldStatus> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/generate-3d/status`);
    if (!res.ok) throw new Error(`Get 3D status failed: ${res.status}`);
    return res.json();
  }
}

export const api = new APIService();

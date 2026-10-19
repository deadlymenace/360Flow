import { Directory, File, Paths } from 'expo-file-system';
import Constants from 'expo-constants';
import * as SecureStore from 'expo-secure-store';
import { Project, StageStatus, StatusResponse, WorldStatus } from '../models/types';
import { TimeoutError, UploadStage, classifyError, errorForStatus } from './uploadPolicy';

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
  // Abort on timeout (not just stop waiting) so a slow upload cannot land after
  // we have decided it failed. The status check in uploadPolicy covers the
  // window where the server already had the full body.
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      fetch(url, { ...options, headers, signal: controller.signal }),
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new TimeoutError(__DEV__ ? 'Request timed out — is the backend running?' : 'Request timed out'));
        }, timeout);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export interface UploadOptions {
  yaws?: number[];
  hfov?: number;
  lens?: string;
  /** 'preparing' while copying photos locally, 'sending' once the POST starts. */
  onStage?: (stage: 'preparing' | 'sending') => void;
}

class APIService {
  private explicitBaseUrl?: string;

  constructor(baseUrl?: string) {
    this.explicitBaseUrl = baseUrl;
  }

  // Resolved lazily so a missing EXPO_PUBLIC_API_URL surfaces as a request
  // error on screen instead of a crash at import time.
  private get baseUrl(): string {
    return this.explicitBaseUrl ?? getBaseUrl();
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

  /**
   * Throws `UploadError`. `stage: 'prepare'` means nothing was sent to the
   * server; `stage: 'send'` means the POST may have reached it. See
   * `uploadPolicy.ts` for why that distinction matters for billing.
   */
  async uploadImages(projectId: string, imageUris: string[], options: UploadOptions = {}): Promise<void> {
    let stage: UploadStage = 'prepare';
    options.onStage?.('preparing');
    const formData = new FormData();
    formData.append(
      'metadata',
      JSON.stringify({
        hfov_deg: options.hfov,
        yaws_deg: options.yaws,
        lens: options.lens,
      })
    );

    // SDK 57 fetch rejects React Native `{ uri, name, type }` parts. A File
    // with a `.jpg` name has image/jpeg type and a bytes() body fetch accepts.
    // The copy keeps the camera original in place for a retry. Each attempt
    // gets its own directory so a second upload cannot overwrite the first.
    const uploadDir = new Directory(
      Paths.cache,
      `upload-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`,
    );
    try {
      uploadDir.create();
      // Register the device before the POST so a token failure is a clean 'prepare' error.
      await deviceToken();
      for (let i = 0; i < imageUris.length; i++) {
        const filename = `segment_${i.toString().padStart(2, '0')}.jpg`;
        const named = new File(uploadDir, filename);
        await new File(imageUris[i]).copy(named, { overwrite: true });
        formData.append('images', named, filename);
      }

      stage = 'send';
      options.onStage?.('sending');
      const res = await fetchWithTimeout(
        `${this.baseUrl}/projects/${projectId}/upload`,
        { method: 'POST', body: formData },
        120000
      );
      if (!res.ok) {
        if (__DEV__) console.warn('Upload failed', res.status, await res.text().catch(() => ''));
        throw errorForStatus(res.status, 'send');
      }
    } catch (e) {
      throw classifyError(e, stage);
    } finally {
      try {
        if (uploadDir.exists) uploadDir.delete();
      } catch {
        // Cache cleanup is best effort.
      }
    }
  }

  async getStatus(projectId: string): Promise<StatusResponse> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/status`);
    if (!res.ok) throw new Error(`Get status failed: ${res.status}`);
    return res.json();
  }

  async downloadToCache(url: string, filename: string): Promise<string> {
    const dest = new File(Paths.cache, filename);
    const output = await File.downloadFileAsync(url, dest, {
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

import { Paths, File as ExpoFile } from 'expo-file-system';
import Constants from 'expo-constants';
import { Project, StatusResponse } from '../models/types';

// Auto-detect the backend URL from the Expo dev server host.
// When running via Expo Go on a physical device, this extracts the host IP
// from the Expo manifest so the app can reach the backend without hardcoding.
function getBaseUrl(): string {
  const debuggerHost =
    Constants.expoConfig?.hostUri ?? Constants.manifest2?.extra?.expoGo?.debuggerHost;
  if (debuggerHost) {
    const host = debuggerHost.split(':')[0]; // strip the Expo port
    return `http://${host}:8000/api/v1`;
  }
  // Fallback for local development / emulator
  return 'http://localhost:8000/api/v1';
}

const BASE_URL = getBaseUrl();

const REQUEST_TIMEOUT = 10000; // 10 seconds

function fetchWithTimeout(url: string, options: RequestInit = {}, timeout = REQUEST_TIMEOUT): Promise<Response> {
  return Promise.race([
    fetch(url, options),
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error('Request timed out — is the backend running?')), timeout)
    ),
  ]);
}

class APIService {
  private baseUrl: string;

  constructor(baseUrl: string = BASE_URL) {
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

  async uploadImages(
    projectId: string,
    imageUris: string[],
    onProgress?: (progress: number) => void
  ): Promise<void> {
    const formData = new FormData();

    for (let i = 0; i < imageUris.length; i++) {
      const uri = imageUris[i];
      const filename = `segment_${i.toString().padStart(2, '0')}.jpg`;

      formData.append('images', {
        uri,
        name: filename,
        type: 'image/jpeg',
      } as any);

      onProgress?.((i + 1) / imageUris.length * 0.5); // 0-50% for prep
    }

    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/upload`, {
      method: 'POST',
      body: formData,
    }, 60000); // 60s timeout for uploads

    onProgress?.(1.0);

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

  async getPanoramaUrl(projectId: string): Promise<string> {
    return `${this.baseUrl}/projects/${projectId}/panorama`;
  }

  async downloadPanorama(projectId: string): Promise<string> {
    const url = `${this.baseUrl}/projects/${projectId}/panorama`;
    const dest = new ExpoFile(Paths.cache, `panorama_${projectId}.jpg`);

    const output = await ExpoFile.downloadFileAsync(url, dest);
    return output.uri;
  }

  async startStaging(projectId: string, prompt: string): Promise<void> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/stage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    });
    if (!res.ok) throw new Error(`Start staging failed: ${res.status}`);
  }

  async getStagingStatus(projectId: string): Promise<{ status: string; prompt: string }> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/stage/status`);
    if (!res.ok) throw new Error(`Get staging status failed: ${res.status}`);
    return res.json();
  }

  async startGenerate3D(projectId: string): Promise<void> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/generate-3d`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`Start 3D generation failed: ${res.status}`);
  }

  async getGenerate3DStatus(projectId: string): Promise<{ status: string; assets: any }> {
    const res = await fetchWithTimeout(`${this.baseUrl}/projects/${projectId}/generate-3d/status`);
    if (!res.ok) throw new Error(`Get 3D status failed: ${res.status}`);
    return res.json();
  }
}

export const api = new APIService();

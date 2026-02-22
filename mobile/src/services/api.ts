import { Paths, File as ExpoFile } from 'expo-file-system';
import { Project, StatusResponse } from '../models/types';

// Change this to your backend server IP when testing on a physical device
// e.g., 'http://192.168.1.100:8000/api/v1'
const BASE_URL = 'http://localhost:8000/api/v1';

class APIService {
  private baseUrl: string;

  constructor(baseUrl: string = BASE_URL) {
    this.baseUrl = baseUrl;
  }

  async createProject(name: string): Promise<Project> {
    const res = await fetch(`${this.baseUrl}/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) throw new Error(`Create project failed: ${res.status}`);
    return res.json();
  }

  async listProjects(): Promise<Project[]> {
    const res = await fetch(`${this.baseUrl}/projects`);
    if (!res.ok) throw new Error(`List projects failed: ${res.status}`);
    return res.json();
  }

  async getProject(projectId: string): Promise<Project> {
    const res = await fetch(`${this.baseUrl}/projects/${projectId}`);
    if (!res.ok) throw new Error(`Get project failed: ${res.status}`);
    return res.json();
  }

  async uploadImages(
    projectId: string,
    imageUris: string[],
    onProgress?: (progress: number) => void
  ): Promise<void> {
    // Build multipart form data manually for progress tracking
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

    const res = await fetch(`${this.baseUrl}/projects/${projectId}/upload`, {
      method: 'POST',
      body: formData,
    });

    onProgress?.(1.0);

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Upload failed: ${res.status} - ${text}`);
    }
  }

  async getStatus(projectId: string): Promise<StatusResponse> {
    const res = await fetch(`${this.baseUrl}/projects/${projectId}/status`);
    if (!res.ok) throw new Error(`Get status failed: ${res.status}`);
    return res.json();
  }

  async getPanoramaUrl(projectId: string): Promise<string> {
    return `${this.baseUrl}/projects/${projectId}/panorama`;
  }

  async downloadPanorama(projectId: string): Promise<string> {
    const url = `${this.baseUrl}/projects/${projectId}/panorama`;
    const file = new ExpoFile(Paths.cache, `panorama_${projectId}.jpg`);

    const res = await fetch(url);
    if (!res.ok) throw new Error(`Download panorama failed: ${res.status}`);

    const blob = await res.blob();
    const buffer = await blob.arrayBuffer();
    file.write(new Uint8Array(buffer));

    return file.uri;
  }

  async startStaging(projectId: string, prompt: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/projects/${projectId}/stage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    });
    if (!res.ok) throw new Error(`Start staging failed: ${res.status}`);
  }

  async getStagingStatus(projectId: string): Promise<{ status: string; prompt: string }> {
    const res = await fetch(`${this.baseUrl}/projects/${projectId}/stage/status`);
    if (!res.ok) throw new Error(`Get staging status failed: ${res.status}`);
    return res.json();
  }

  async startGenerate3D(projectId: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/projects/${projectId}/generate-3d`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`Start 3D generation failed: ${res.status}`);
  }

  async getGenerate3DStatus(projectId: string): Promise<{ status: string; assets: any }> {
    const res = await fetch(`${this.baseUrl}/projects/${projectId}/generate-3d/status`);
    if (!res.ok) throw new Error(`Get 3D status failed: ${res.status}`);
    return res.json();
  }
}

export const api = new APIService();

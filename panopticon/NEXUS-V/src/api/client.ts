import type { Camera, DetectionEvent, ModuleStatus, ResourceSnapshot } from "../types";

const API = "";

export async function fetchCameras(): Promise<Camera[]> {
  const res = await fetch(`${API}/api/cameras`);
  if (!res.ok) throw new Error("Erreur chargement caméras");
  return res.json();
}

export async function fetchModules(): Promise<ModuleStatus[]> {
  const res = await fetch(`${API}/api/daemon/modules`);
  if (!res.ok) throw new Error("Erreur chargement modules");
  return res.json();
}

export async function fetchResources(): Promise<ResourceSnapshot> {
  const res = await fetch(`${API}/api/daemon/resources`);
  if (!res.ok) throw new Error("Erreur ressources");
  return res.json();
}

export async function fetchEvents(limit = 50): Promise<DetectionEvent[]> {
  const res = await fetch(`${API}/api/events?limit=${limit}`);
  if (!res.ok) throw new Error("Erreur événements");
  return res.json();
}

export async function startModule(name: string): Promise<void> {
  await fetch(`${API}/api/daemon/modules/${name}/start`, { method: "POST" });
}

export async function stopModule(name: string): Promise<void> {
  await fetch(`${API}/api/daemon/modules/${name}/stop`, { method: "POST" });
}

export async function createCamera(data: {
  name: string;
  connection_url: string;
  zone: string;
  target_fps: number;
}): Promise<Camera> {
  const res = await fetch(`${API}/api/cameras`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Erreur création caméra");
  return res.json();
}

export function cameraStreamUrl(cameraId: string): string {
  return `${API}/api/cameras/${cameraId}/stream?t=${Date.now()}`;
}

export function connectLive(onMessage: (data: unknown) => void): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${window.location.host}/ws/live`);
  ws.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch {
      /* ignore */
    }
  };
  return ws;
}

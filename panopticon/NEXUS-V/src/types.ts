export interface Camera {
  id: string;
  name: string;
  connection_url: string;
  zone: string;
  target_fps: number;
  status: "online" | "offline" | "reconnecting";
  created_at: string;
}

export interface ModuleStatus {
  name: string;
  status: "running" | "stopped" | "crashed" | "starting";
  cpu_percent: number | null;
  ram_mb: number | null;
  started_at: string | null;
  message: string | null;
}

export interface DetectionEvent {
  id: string;
  camera_id: string;
  source_module: string;
  event_type: string;
  zone: string;
  timestamp: string;
  thumbnail_path: string | null;
  metadata: Record<string, unknown>;
}

export interface ResourceSnapshot {
  cpu_percent: number;
  ram_total_mb: number;
  ram_available_mb: number;
  ram_used_mb: number;
  gpu_available: boolean;
  gpu_name: string | null;
}

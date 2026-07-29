import { useCallback, useEffect, useState } from "react";
import {
  connectLive,
  fetchCameras,
  fetchEvents,
  fetchModules,
  fetchResources,
  startModule,
  stopModule,
} from "./api/client";
import { AddCameraForm } from "./components/AddCameraForm";
import { CameraGrid } from "./components/CameraGrid";
import { EventTimeline } from "./components/EventTimeline";
import { ModulePanel } from "./components/ModulePanel";
import type { Camera, DetectionEvent, ModuleStatus, ResourceSnapshot } from "./types";

export default function App() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [modules, setModules] = useState<ModuleStatus[]>([]);
  const [resources, setResources] = useState<ResourceSnapshot | null>(null);
  const [events, setEvents] = useState<DetectionEvent[]>([]);

  const refresh = useCallback(async () => {
    const [c, m, r, e] = await Promise.all([
      fetchCameras(),
      fetchModules(),
      fetchResources(),
      fetchEvents(),
    ]);
    setCameras(c);
    setModules(m);
    setResources(r);
    setEvents(e);
  }, []);

  useEffect(() => {
    refresh().catch(console.error);
    const ws = connectLive((msg: unknown) => {
      const data = msg as { type: string; modules?: ModuleStatus[]; resources?: ResourceSnapshot; data?: DetectionEvent };
      if (data.type === "status") {
        if (data.modules) setModules(data.modules);
        if (data.resources) setResources(data.resources);
      }
      if (data.type === "event" && data.data) {
        setEvents((prev) => [data.data!, ...prev].slice(0, 100));
      }
    });
    const interval = setInterval(refresh, 5000);
    return () => {
      ws.close();
      clearInterval(interval);
    };
  }, [refresh]);

  async function handleToggle(name: string, running: boolean) {
    if (running) await stopModule(name);
    else await startModule(name);
    await refresh();
  }

  return (
    <div className="min-h-screen p-4 md:p-6 max-w-7xl mx-auto space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">NEXUS-V</h1>
          <p className="text-gray-400 text-sm">Dashboard PANOPTICON · Phase 1 MVP</p>
        </div>
        {resources && (
          <div className="text-xs text-gray-400 font-mono">
            CPU {resources.cpu_percent.toFixed(0)}% · RAM{" "}
            {resources.ram_available_mb.toFixed(0)}/{resources.ram_total_mb.toFixed(0)} MB
            {resources.gpu_available && resources.gpu_name && ` · GPU ${resources.gpu_name}`}
          </div>
        )}
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <CameraGrid cameras={cameras} />
        </div>
        <div className="space-y-6">
          <ModulePanel modules={modules} onToggle={handleToggle} />
          <AddCameraForm onCreated={(cam) => setCameras((p) => [...p, cam])} />
        </div>
      </div>

      <EventTimeline events={events} />
    </div>
  );
}

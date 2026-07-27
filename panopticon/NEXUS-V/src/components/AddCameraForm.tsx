import { FormEvent, useState } from "react";
import { createCamera } from "../api/client";
import type { Camera } from "../types";

interface Props {
  onCreated: (camera: Camera) => void;
}

export function AddCameraForm({ onCreated }: Props) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("0");
  const [zone, setZone] = useState("default");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const cam = await createCamera({
        name,
        connection_url: url,
        zone,
        target_fps: 3,
      });
      onCreated(cam);
      setName("");
      setUrl("0");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="bg-panopticon-panel rounded-lg p-4 space-y-3">
      <h2 className="text-lg font-semibold">Ajouter une caméra</h2>
      <p className="text-xs text-gray-400">
        Utilisez <code className="bg-gray-800 px-1 rounded">0</code> pour la webcam locale,
        ou une URL RTSP (<code className="bg-gray-800 px-1 rounded">rtsp://…</code>).
      </p>
      <input
        className="w-full bg-gray-800 rounded px-3 py-2 text-sm"
        placeholder="Nom"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
      />
      <input
        className="w-full bg-gray-800 rounded px-3 py-2 text-sm"
        placeholder="URL (0 = webcam)"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        required
      />
      <input
        className="w-full bg-gray-800 rounded px-3 py-2 text-sm"
        placeholder="Zone"
        value={zone}
        onChange={(e) => setZone(e.target.value)}
      />
      {error && <p className="text-panopticon-err text-sm">{error}</p>}
      <button
        type="submit"
        disabled={loading}
        className="w-full bg-panopticon-accent hover:bg-blue-600 rounded py-2 text-sm font-medium disabled:opacity-50"
      >
        {loading ? "Création…" : "Créer"}
      </button>
    </form>
  );
}

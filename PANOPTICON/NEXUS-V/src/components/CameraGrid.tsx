import type { Camera } from "../types";
import { cameraStreamUrl } from "../api/client";

interface Props {
  cameras: Camera[];
}

export function CameraGrid({ cameras }: Props) {
  if (cameras.length === 0) {
    return (
      <section className="bg-panopticon-panel rounded-lg p-6 text-center text-gray-400">
        Aucune caméra configurée. Ajoutez-en une via l'API ou le formulaire ci-dessous.
      </section>
    );
  }

  return (
    <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {cameras.map((cam) => (
        <article key={cam.id} className="bg-panopticon-panel rounded-lg overflow-hidden">
          <div className="aspect-video bg-black relative">
            <img
              src={cameraStreamUrl(cam.id)}
              alt={cam.name}
              className="w-full h-full object-contain"
              onError={(e) => {
                (e.target as HTMLImageElement).style.opacity = "0.3";
              }}
            />
          </div>
          <div className="p-3 flex justify-between text-sm">
            <div>
              <h3 className="font-semibold">{cam.name}</h3>
              <p className="text-gray-400">{cam.zone}</p>
            </div>
            <span
              className={
                cam.status === "online"
                  ? "text-panopticon-ok"
                  : cam.status === "reconnecting"
                    ? "text-panopticon-warn"
                    : "text-panopticon-err"
              }
            >
              {cam.status}
            </span>
          </div>
        </article>
      ))}
    </section>
  );
}

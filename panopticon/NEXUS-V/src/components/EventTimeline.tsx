import type { DetectionEvent } from "../types";

interface Props {
  events: DetectionEvent[];
}

export function EventTimeline({ events }: Props) {
  return (
    <section className="bg-panopticon-panel rounded-lg p-4">
      <h2 className="text-lg font-semibold mb-3">Timeline</h2>
      {events.length === 0 ? (
        <p className="text-gray-400 text-sm">Aucun événement pour l'instant.</p>
      ) : (
        <ul className="space-y-2 max-h-96 overflow-y-auto text-sm">
          {events.map((ev) => (
            <li key={ev.id} className="border-l-2 border-panopticon-accent pl-3 py-1">
              <div className="flex justify-between gap-2">
                <span className="font-mono text-panopticon-accent">{ev.event_type}</span>
                <time className="text-gray-500 text-xs">
                  {new Date(ev.timestamp).toLocaleString("fr-FR")}
                </time>
              </div>
              <p className="text-gray-400">
                {ev.zone} · {ev.source_module}
                {ev.metadata?.label != null && ` · ${String(ev.metadata.label)}`}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

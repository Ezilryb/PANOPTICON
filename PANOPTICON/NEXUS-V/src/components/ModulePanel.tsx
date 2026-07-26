import type { ModuleStatus } from "../types";

const STATUS_COLOR: Record<string, string> = {
  running: "text-panopticon-ok",
  stopped: "text-gray-400",
  crashed: "text-panopticon-err",
  starting: "text-panopticon-warn",
};

interface Props {
  modules: ModuleStatus[];
  onToggle: (name: string, running: boolean) => void;
}

export function ModulePanel({ modules, onToggle }: Props) {
  return (
    <section className="bg-panopticon-panel rounded-lg p-4">
      <h2 className="text-lg font-semibold mb-3">Modules DAEMON</h2>
      <ul className="space-y-2">
        {modules.map((m) => (
          <li
            key={m.name}
            className="flex items-center justify-between gap-2 text-sm border-b border-gray-700 pb-2"
          >
            <div>
              <span className="font-mono uppercase">{m.name}</span>
              <span className={`ml-2 ${STATUS_COLOR[m.status] ?? ""}`}>
                {m.status}
              </span>
              {m.ram_mb != null && (
                <span className="ml-2 text-gray-500">{m.ram_mb.toFixed(0)} MB</span>
              )}
              {m.message && (
                <p className="text-xs text-panopticon-warn mt-1">{m.message}</p>
              )}
            </div>
            {m.name !== "nexus_v" && (
              <button
                className="px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 text-xs"
                onClick={() => onToggle(m.name, m.status === "running")}
              >
                {m.status === "running" ? "Stop" : "Start"}
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

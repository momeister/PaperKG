/** Probelauf — eine git-worktree-Sandbox zum gefahrlosen Experimentieren.

 * Auf einem Checkpoint angelegt, mit eigenem Testbefehl, mit Übernahme in den
 * Hauptbaum nur auf Knopfdruck (und Checkpoint davor). Ignorierte Ordner
 * (node_modules, .venv) fehlen im Worktree — das steht hier, nicht im
 * Kleingedruckten, denn ein ``npm test`` scheitert sonst scheinbar grundlos.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { Sandbox } from "../../types";

export function SandboxPanel({ projectId }: { projectId: string | null }) {
  const qc = useQueryClient();
  const [command, setCommand] = useState("");
  const [active, setActive] = useState<Sandbox | null>(null);
  const [runOutput, setRunOutput] = useState<string | null>(null);

  const { data: checkpointsData } = useQuery({
    queryKey: ["codegraph", "checkpoints", projectId],
    queryFn: () => api.werkstatt.listCheckpoints(projectId!),
    enabled: Boolean(projectId),
  });
  const { data, refetch } = useQuery({
    queryKey: ["codegraph", "sandboxes", projectId],
    queryFn: () => api.werkstatt.listSandboxes(projectId!),
    enabled: Boolean(projectId),
  });

  const create = useMutation({
    mutationFn: ({ checkpointId, cmd }: { checkpointId: string; cmd?: string }) =>
      api.werkstatt.createSandbox(projectId!, checkpointId, cmd),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["codegraph", "sandboxes", projectId] }),
  });

  const run = useMutation({
    mutationFn: ({ sandboxId, cmd }: { sandboxId: string; cmd?: string }) =>
      api.werkstatt.runSandbox(projectId!, sandboxId, cmd),
    onSuccess: (r) => {
      setRunOutput(`$ ${r.run.command.join(" ")}\n[exit ${r.run.returncode}, ${r.run.duration_s}s]\n\n${r.run.stdout}\n${r.run.stderr}`);
      qc.invalidateQueries({ queryKey: ["codegraph", "sandboxes", projectId] });
    },
  });

  const apply = useMutation({
    mutationFn: (sandboxId: string) => api.werkstatt.applySandbox(projectId!, sandboxId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["codegraph", "sandboxes", projectId] }),
  });

  const remove = useMutation({
    mutationFn: (sandboxId: string) => api.werkstatt.deleteSandbox(projectId!, sandboxId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["codegraph", "sandboxes", projectId] }),
  });

  if (!projectId) return <div className="cgp-empty">Wähle ein Projekt.</div>;
  const sandboxes = data?.sandboxes ?? [];
  const latestCheckpoint = checkpointsData?.checkpoints?.[0];

  return (
    <div className="cg-sandbox">
      <div className="cg-sandbox-bar">
        <button
          className="wk-btn"
          disabled={!latestCheckpoint || create.isPending}
          onClick={() =>
            latestCheckpoint &&
            create.mutate({ checkpointId: latestCheckpoint.id, cmd: command || undefined })
          }
          title={latestCheckpoint ? `auf ${latestCheckpoint.label ?? "Checkpoint"}` : "erst einen Checkpoint anlegen"}
        >
          Sandbox anlegen
        </button>
        <input
          className="cg-sandbox-cmd"
          placeholder="Testbefehl (leer = erkennen: pytest / npm test / cargo test)"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
        />
        <button className="wk-btn" onClick={() => refetch()}>
          Aktualisieren
        </button>
      </div>
      <div className="cg-sandbox-hint">
        Hinweis: ignorierte Ordner (node_modules, .venv, target) fehlen im Worktree — ein Testbefehl
        muss sie ggf. erst installieren.
      </div>

      {sandboxes.length === 0 && (
        <div className="cgp-empty">Noch keine Sandbox. Lege eine aus dem neuesten Checkpoint an.</div>
      )}
      {sandboxes.map((sb) => (
        <div key={sb.id} className="cg-sandbox-row">
          <div className="cg-sandbox-meta">
            <span className={`cg-sandbox-status cg-sandbox-status--${sb.status}`}>{sb.status}</span>
            <span className="cg-sandbox-base">{sb.base_sha ? sb.base_sha.slice(0, 8) : ""}</span>
            {sb.last_exit_code !== null && (
              <span className={sb.last_exit_code === 0 ? "cg-sandbox-ok" : "cg-sandbox-fail"}>
                exit {sb.last_exit_code}
              </span>
            )}
          </div>
          <div className="cg-sandbox-actions">
            <button
              disabled={run.isPending}
              onClick={() => {
                setActive(sb);
                run.mutate({ sandboxId: sb.id, cmd: command || undefined });
              }}
            >
              Tests ausführen
            </button>
            <button onClick={() => apply.mutate(sb.id)} title="Geänderte Pfade in den Hauptbaum übernehmen (mit Checkpoint davor)">
              Übernehmen
            </button>
            <button onClick={() => remove.mutate(sb.id)}>Verwerfen</button>
          </div>
        </div>
      ))}

      {runOutput && active && (
        <pre className="cg-sandbox-output">
          <button className="cg-sandbox-close" onClick={() => setRunOutput(null)}>
            schließen
          </button>
          {runOutput}
        </pre>
      )}
    </div>
  );
}
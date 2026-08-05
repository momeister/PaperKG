/** Git-Checkpoints — ein Weg zurueck, sichtbar in jedem /code-Tab.

 * Ein Rücksprung, den man suchen muss, ist keiner. Deshalb haengt die Leiste
 * direkt unter der Kopfzeile, nicht in einem Dialog. Sie sichert manuell und
 * listet die letzten Checkpoints (manuelle + automatische vor einem Schreiben)
 * mit Restore-Vorschau und Loeschen.
 *
 * Fail-soft: ohne git (reason ``no_git`` / ``no_repo``) steht das da, statt
 * die Leiste zu verstecken — der Nutzer muss wissen, dass es keinen Rücksprung
 * gibt.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { Checkpoint, CheckpointRestorePlan } from "../../types";

const REASON_LABEL: Record<string, string> = {
  manual: "manuell",
  auto_symbol_write: "vor Symbol-Schreibung",
  auto_file_write: "vor Speichern",
  auto_refactor: "vor Refactor",
  auto_sandbox_apply: "vor Sandbox-Übernahme",
  pre_restore: "vor Rücksprung",
};

function formatTime(ts: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString("de-DE", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
}

export function CheckpointBar({ projectId }: { projectId: string | null }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<{ id: string; plan: CheckpointRestorePlan } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ["codegraph", "checkpoints", projectId],
    queryFn: () => api.werkstatt.listCheckpoints(projectId!),
    enabled: Boolean(projectId),
  });

  const checkpoints = data?.checkpoints ?? [];

  const create = useMutation({
    mutationFn: (label: string) => api.werkstatt.createCheckpoint(projectId!, label),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["codegraph", "checkpoints", projectId] });
    },
    onError: (e: unknown) => setError(String(e)),
  });

  const restore = useMutation({
    mutationFn: ({ id, planHash }: { id: string; planHash: string }) =>
      api.werkstatt.restoreCheckpoint(projectId!, id, planHash),
    onSuccess: () => {
      setPreview(null);
      setOpen(false);
      setError(null);
      qc.invalidateQueries({ queryKey: ["codegraph", "checkpoints", projectId] });
    },
    onError: (e: unknown) => setError(String(e)),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.werkstatt.deleteCheckpoint(projectId!, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["codegraph", "checkpoints", projectId] }),
  });

  if (!projectId) return null;

  return (
    <div className="cg-checkpoint-bar">
      <button
        className="cg-ckpt-sichern"
        onClick={() => {
          const label = window.prompt("Bezeichnung des Checkpoints?", "manueller Stand");
          if (label) create.mutate(label);
        }}
        disabled={create.isPending}
      >
        ⏸ Stand sichern
      </button>
      <button className="cg-ckpt-toggle" onClick={() => setOpen((v) => !v)}>
        {checkpoints.length} Checkpoint{checkpoints.length === 1 ? "" : "s"} {open ? "▲" : "▼"}
      </button>
      {error && <span className="cg-ckpt-error">{error}</span>}
      {open && (
        <div className="cg-ckpt-list">
          {checkpoints.length === 0 && (
            <div className="cg-ckpt-empty">Noch keine Checkpoints. Ein Schreiben legt automatisch einen an.</div>
          )}
          {checkpoints.map((c: Checkpoint) => (
            <div key={c.id} className="cg-ckpt-row">
              <div className="cg-ckpt-meta">
                <span className="cg-ckpt-reason">{REASON_LABEL[c.reason] ?? c.reason}</span>
                <span className="cg-ckpt-label">{c.label ?? ""}</span>
                <span className="cg-ckpt-time">{formatTime(c.created_timestamp)}</span>
                <span className="cg-ckpt-files">{c.file_count} Dateien</span>
              </div>
              <div className="cg-ckpt-actions">
                <button
                  onClick={async () => {
                    const p = await api.werkstatt.checkpointRestorePreview(projectId, c.id);
                    setPreview({ id: c.id, plan: p.plan });
                  }}
                >
                  Vorschau
                </button>
                <button onClick={() => remove.mutate(c.id)} title="Checkpoint löschen">
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {preview && (
        <div className="cg-ckpt-preview">
          <div className="cg-ckpt-preview-head">
            Rücksprung auf diesen Stand — {preview.plan.entries.length} Pfad(e) berührt.
            <button className="cg-ckpt-cancel" onClick={() => setPreview(null)}>
              Abbrechen
            </button>
          </div>
          <ul className="cg-ckpt-preview-list">
            {preview.plan.entries.map((e) => (
              <li key={e.path}>
                <code>{e.status}</code> {e.path}
              </li>
            ))}
          </ul>
          <div className="cg-ckpt-preview-note">
            Vor dem Rücksprung wird automatisch ein weiterer Checkpoint angelegt.
          </div>
          <button
            className="cg-ckpt-apply"
            disabled={restore.isPending}
            onClick={() =>
              restore.mutate({ id: preview.id, planHash: preview.plan.plan_hash })
            }
          >
            Rücksprung ausführen
          </button>
        </div>
      )}
    </div>
  );
}
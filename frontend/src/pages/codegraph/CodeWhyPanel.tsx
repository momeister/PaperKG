/**
 * „Warum wurde das so gebaut?" — in zwei Blöcken, die nie ineinanderlaufen.
 *
 * Der Vorbehalt bestimmt die Bauform: der Prompt einer erzeugenden KI ist in
 * diesem Repository **nirgends aufgezeichnet**. Ein Werkzeug, das so täte, als
 * könne es ihn rekonstruieren, wäre die gefährlichste unbelegte Behauptung im
 * ganzen Programm — eine erfundene `datei:zeile` fällt beim Nachschlagen auf,
 * eine erfundene Absicht nicht.
 *
 * Deshalb:
 *
 *   * **Aufgezeichnet** steht oben, kommt ohne Modell aus und ist sofort da:
 *     `git log -L` über genau diese Zeilen (nicht über die Datei — ein
 *     Datei-Log nennt hundert Commits, die woanders etwas geändert haben) und
 *     die selbst hinterlegten Begründungen.
 *   * **Hergeleitet** steht darunter, ist als Herleitung beschriftet, nennt
 *     worauf es beruht, und wird nicht gespeichert.
 *
 * Und ein Feld zum Festhalten. Das ist der Teil, der das Problem langfristig
 * löst: ab hier gibt es eine Aufzeichnung.
 */
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, GitCommitHorizontal, HelpCircle, Sparkles, Trash2 } from "lucide-react";

import { api, streamCodeWhy } from "../../api";
import type { CodeDerivedRationale, CodeNodeId, CodeWhyEvent } from "../../types";
import { AnswerText } from "../CodeAskPanel";

/** Warum es keine Historie gibt. Nichts davon ist ein Fehler. */
const NO_HISTORY: Record<string, string> = {
  no_git: "Auf diesem Rechner ist kein git installiert.",
  no_repo: "Dieser Ordner ist kein git-Repository — es gibt keine Historie.",
  no_commits: "Dieses Repository hat noch keinen Commit.",
  untracked: "Diese Datei wurde nie committet.",
  error: "Die Historie liess sich nicht lesen.",
};

export type CodeWhyPanelProps = {
  projectId: string;
  nodeId: CodeNodeId;
  provider?: string | null;
  model?: string | null;
  onOpen: (path: string, line: number) => void;
};

export function CodeWhyPanel({
  projectId,
  nodeId,
  provider = null,
  model = null,
  onOpen,
}: CodeWhyPanelProps) {
  const queryClient = useQueryClient();
  const [derived, setDerived] = useState<CodeDerivedRationale | null>(null);
  const [state, setState] = useState<"idle" | "running" | "failed">("idle");
  const [note, setNote] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const recorded = useQuery({
    queryKey: ["codegraph", "why", projectId, nodeId],
    queryFn: () => api.codegraph.recordedRationale(projectId, nodeId),
  });

  const keep = useMutation({
    mutationFn: (text: string) =>
      api.codegraph.addRationale(projectId, { text, symbol_id: nodeId }),
    onSuccess: () => {
      setDraft("");
      void queryClient.invalidateQueries({ queryKey: ["codegraph", "why", projectId, nodeId] });
    },
  });

  const forget = useMutation({
    mutationFn: (id: string) => api.codegraph.removeRationale(projectId, id),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["codegraph", "why", projectId, nodeId] }),
  });

  function derive() {
    if (state === "running") return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState("running");
    setDerived(null);
    setNote("lese die Stelle");
    void streamCodeWhy(
      projectId,
      nodeId,
      { provider, model },
      (event: CodeWhyEvent) => {
        if (event.event === "activity") setNote(event.text);
        else if (event.event === "failed") {
          setState("failed");
          setNote(event.error);
        } else {
          setDerived(event.answer);
          setState("idle");
          setNote(null);
        }
      },
      controller.signal,
    ).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setState("failed");
      setNote(error instanceof Error ? error.message : String(error));
    });
  }

  const history = recorded.data?.history;
  const notes = recorded.data?.notes ?? [];

  return (
    <section className="cg-section cgp-why-panel">
      <h4>
        Warum das so gebaut ist
        <button
          className="wk-btn cgp-explain-btn"
          onClick={derive}
          disabled={state === "running"}
          title="Ein Modell leitet aus Code, Tests, Aufrufern und Commit-Betreffen zwei Sätze her. Eine Herleitung ist kein Beleg."
        >
          <Sparkles size={13} />
          {state === "running" ? "leitet ab …" : derived ? "neu herleiten" : "herleiten"}
        </button>
      </h4>

      {/* --- Aufgezeichnet ------------------------------------------------ */}
      <div className="cgp-why-block cgp-why-block--recorded">
        <span className="cgp-why-tag">Aufgezeichnet</span>

        {recorded.isLoading ? (
          <p className="muted cg-fineprint">wird gelesen …</p>
        ) : history?.available && history.commits.length ? (
          <ul className="cgp-commits">
            {history.commits.map((commit) => (
              <li key={commit.hash}>
                <GitCommitHorizontal size={12} />
                <code>{commit.hash}</code>
                <span className="cgp-commit-subject">{commit.subject}</span>
                <span className="cg-row-meta">
                  {commit.date} · {commit.author}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted cg-fineprint">
            {NO_HISTORY[history?.reason ?? "error"] ??
              "Keine Commits haben genau diese Zeilen angefasst."}
          </p>
        )}

        {notes.map((entry) => (
          <div key={entry.id} className={`cgp-kept${entry.stale ? " cgp-kept--stale" : ""}`}>
            <p>{entry.text}</p>
            <span className="cg-row-meta">
              {entry.author || "hinterlegt"}
              {entry.created_timestamp ? ` · ${entry.created_timestamp.slice(0, 10)}` : ""}
              {entry.stale && (
                <em title="Die Datei hat sich seit dem Festhalten geändert — die Begründung gilt für einen anderen Stand.">
                  {" "}
                  · überholt
                </em>
              )}
            </span>
            <button
              className="cgp-iconlink"
              onClick={() => forget.mutate(entry.id)}
              title="Begründung löschen"
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}

        <div className="cgp-keep">
          <textarea
            rows={2}
            value={draft}
            placeholder="Begründung festhalten — warum ist das so und nicht anders?"
            onChange={(event) => setDraft(event.target.value)}
          />
          <button
            className="wk-btn"
            disabled={!draft.trim() || keep.isPending}
            onClick={() => keep.mutate(draft.trim())}
          >
            Festhalten
          </button>
        </div>
        {keep.isError && (
          <p className="cg-notice cg-notice--error">
            <AlertTriangle size={14} />{" "}
            <span>{keep.error instanceof Error ? keep.error.message : "nicht gespeichert"}</span>
          </p>
        )}
      </div>

      {/* --- Hergeleitet -------------------------------------------------- */}
      {(state !== "idle" || derived) && (
        <div className="cgp-why-block cgp-why-block--derived">
          <span className="cgp-why-tag cgp-why-tag--derived">
            <HelpCircle size={11} /> Hergeleitet
          </span>

          {state === "running" && (
            <p className="muted cgp-explain-note">
              <Sparkles size={12} /> {note}
            </p>
          )}
          {state === "failed" && (
            <p className="cg-notice cg-notice--error cgp-explain-note">
              <AlertTriangle size={14} /> <span>{note}</span>
            </p>
          )}
          {derived && (
            <>
              <AnswerText text={derived.text} citations={derived.citations} onOpen={onOpen} />
              {/* Worauf es beruht, als Zahlen. „Vertrau mir" wäre hier genau die
                  Behauptung, gegen die das Werkzeug gebaut ist. */}
              <p className="cg-row-meta">
                aus {derived.based_on.commits} Commits, {derived.based_on.tests} Tests,{" "}
                {derived.based_on.callers} Aufrufern
                {derived.based_on.notes ? `, ${derived.based_on.notes} Begründungen` : ""} ·{" "}
                {derived.verdict_label ?? "geprüft"} · {derived.model}
              </p>
            </>
          )}
        </div>
      )}
    </section>
  );
}

export default CodeWhyPanel;

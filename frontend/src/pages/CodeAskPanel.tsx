/**
 * Der Begleiter: Fragen an eine fremde Codebase, mit geprüften Belegen.
 *
 * Die eine Sache, die diese Anzeige richtig machen muss: **ein ungeprüftes Zitat
 * darf nie aussehen wie ein geprüftes.** Jeder Beleg-Chip trägt seinen Status —
 * belegt / nicht nachgeschlagen / Datei geändert / Datei unbekannt —, und das
 * Urteil über die ganze Antwort steht darüber. „Keine Belege genannt" ist dabei
 * ausdrücklich *nicht* dasselbe wie „stimmt": es hieße nur, dass es nichts zu
 * prüfen gab, und das als „geprüft" auszugeben wäre genau der Fehler, gegen den
 * die Prüfung existiert.
 *
 * Die Spur am Ende ist der eigentliche Ertrag. Ein Absatz Prosa über einen Fehler
 * ist schwer nachzuprüfen; vier Dateipositionen in der Reihenfolge, in der die
 * Daten fließen, geht man in einer Minute selbst ab.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  History,
  Send,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";

import { api, streamCodeAsk } from "../api";
import { noteProjectId } from "../projectScope";
import type {
  CodeAnswer,
  CodeAnswerRecord,
  CodeAskEvent,
  CodeCitation,
  CodeCitationStatus,
  CodeTrailStep,
  CodeVerdict,
} from "../types";

/** Wortlaut wie im Original — die Formulierungen sind Teil der Aussage. */
const CITATION_META: Record<CodeCitationStatus, { label: string; hint: string }> = {
  verified: {
    label: "belegt",
    hint: "Diese Zeilen wurden im Gespräch tatsächlich nachgeschlagen und die Datei ist unverändert.",
  },
  not_retrieved: {
    label: "nicht nachgeschlagen",
    hint: "Die Datei gibt es, aber diese Zeilen hat das Modell nie bekommen. Nicht darauf verlassen.",
  },
  stale: {
    label: "Datei geändert",
    hint: "Die Datei hat sich seit dem Einlesen geändert — die Zeilennummer zeigt nicht mehr dorthin.",
  },
  unknown_file: {
    label: "Datei unbekannt",
    hint: "Diese Datei existiert im Projekt nicht. Das Modell hat sie erfunden.",
  },
};

const VERDICT_META: Record<CodeVerdict, { label: string; tone: "ok" | "warn" }> = {
  sound: { label: "Alle Belege halten", tone: "ok" },
  uncited: { label: "keine Belege genannt — nichts zu prüfen", tone: "warn" },
  broken: { label: "mindestens ein Beleg hält nicht", tone: "warn" },
};

export type CodeAskFocus = {
  id: string;
  qualified: string;
  path: string;
  line: number;
};

export type CodeAskPanelProps = {
  projectId: string;
  /** Springt in den Monaco-Editor der Werkstatt, auf Datei und Zeile. */
  onOpenSymbol?: (path: string, line: number) => void;
  /** Forschungsprojekt für die Paper-Evidenz, falls eines aktiv ist. */
  researchProjectId?: string | null;
  /**
   * Anbieter/Modell für diese Frage. `null` heisst „entscheide nach config.yaml"
   * — nicht dasselbe wie „nimm den zuletzt gewählten": eine eingefrorene Wahl
   * wäre falsch, sobald der Nutzer oben umstellt.
   */
  provider?: string | null;
  model?: string | null;
  /** Gewähltes Symbol; hängt sichtbar als Kontext an der Frage. */
  focus?: CodeAskFocus | null;
  /** Springt einen Spur-Schritt auf der Karte an (nur die grosse Seite kann das). */
  onShowOnMap?: (nodeId: string) => void;
};

export function CodeAskPanel({
  projectId,
  onOpenSymbol,
  researchProjectId,
  provider = null,
  model = null,
  focus = null,
  onShowOnMap,
}: CodeAskPanelProps) {
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState("");
  const [usePapers, setUsePapers] = useState(false);
  const [useFocus, setUseFocus] = useState(true);
  const [activity, setActivity] = useState<string[]>([]);
  const [answer, setAnswer] = useState<CodeAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  // Ein Gespräch. Auf der Rust-Seite hängt daran die Lizenz zum Zitieren.
  const sessionRef = useRef(`s_${Math.random().toString(36).slice(2)}`);

  const history = useQuery({
    queryKey: ["codegraph", "answers", projectId],
    queryFn: () => api.codegraph.answers(projectId, 30),
    enabled: showHistory,
  });

  const ask = useMutation({
    mutationFn: async (text: string) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setActivity([]);
      setAnswer(null);
      setError(null);
      await streamCodeAsk(
        projectId,
        {
          question: text,
          session: sessionRef.current,
          // `null` heisst: der Router entscheidet nach config.yaml. Genau das
          // soll „erbt global" bewirken — eine hier eingefrorene Wahl wäre
          // falsch, sobald oben umgestellt wird.
          provider,
          model,
          project_id: researchProjectId ?? null,
          use_papers: usePapers,
        },
        (event: CodeAskEvent) => {
          if (event.event === "activity") {
            setActivity((previous) => [...previous, event.text]);
          } else if (event.event === "failed") {
            setError(event.error);
          } else if (event.event === "done") {
            setAnswer(event.answer);
          }
        },
        controller.signal,
      );
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["codegraph", "answers", projectId] });
    },
  });

  useEffect(() => {
    setAnswer(null);
    setActivity([]);
    setError(null);
    sessionRef.current = `s_${Math.random().toString(36).slice(2)}`;
  }, [projectId]);

  useEffect(() => () => abortRef.current?.abort(), []);

  function submit() {
    const text = question.trim();
    if (!text || ask.isPending) return;
    // Der Kontext steht als Chip über dem Feld, bevor er mitgeschickt wird —
    // eine still angehängte Vorbemerkung wäre eine Frage, die der Nutzer nicht
    // gestellt hat.
    const prefixed =
      focus && useFocus ? `Zu \`${focus.qualified}\` (${focus.path}:${focus.line}): ${text}` : text;
    ask.mutate(prefixed);
  }

  return (
    <div className="cg-ask">
      <div className="cg-ask-input">
        {focus && useFocus && (
          <span className="cgp-ask-context" title="Wird der Frage vorangestellt">
            Zu <code>{focus.qualified}</code> ({focus.path}:{focus.line})
            <button onClick={() => setUseFocus(false)} title="Kontext weglassen">
              ✕
            </button>
          </span>
        )}
        {focus && !useFocus && (
          <button className="cgp-ask-context cgp-ask-context--off" onClick={() => setUseFocus(true)}>
            Kontext <code>{focus.qualified}</code> wieder anhängen
          </button>
        )}
        <textarea
          rows={focus ? 3 : 2}
          value={question}
          placeholder="Warum ist das so gebaut? Wer ruft das auf? Wo fließen die Daten hin?"
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
        />
        <div className="cg-ask-actions">
          <label className="cg-ask-toggle" title="Zieht passende Auszüge aus der lokalen Paper-Sammlung dazu. Papers werden mit [arxiv:…] zitiert, Code mit pfad:zeile.">
            <input
              type="checkbox"
              checked={usePapers}
              onChange={(event) => setUsePapers(event.target.checked)}
            />
            <BookOpen size={13} /> Papers einbeziehen
          </label>
          <span className="cg-tabs-spacer" />
          <button
            className="wk-iconbtn"
            title="Frühere Fragen"
            onClick={() => setShowHistory((value) => !value)}
          >
            <History size={14} />
          </button>
          <button className="wk-btn" onClick={submit} disabled={ask.isPending || !question.trim()}>
            <Send size={14} /> {ask.isPending ? "sucht …" : "Fragen"}
          </button>
        </div>
      </div>

      <div className="cg-scroll">
        {ask.isPending && (
          <ul className="cg-activity">
            {activity.map((line, index) => (
              <li key={`${line}-${index}`}>
                <Sparkles size={12} /> {line}
              </li>
            ))}
            {activity.length === 0 && <li className="muted">bereite vor …</li>}
          </ul>
        )}

        {error && (
          <div className="cg-notice cg-notice--error">
            <AlertTriangle size={15} />
            <span>{error}</span>
          </div>
        )}

          {answer && (
          <AnswerView
            answer={answer}
            onOpen={onOpenSymbol}
            onShowOnMap={onShowOnMap}
            projectId={projectId}
            notesProjectId={noteProjectId(researchProjectId ?? undefined)}
          />
        )}

        {showHistory && (
          <section className="cg-section">
            <h4>
              <History size={13} /> Frühere Fragen
            </h4>
            {history.isLoading && <p className="muted">wird geladen …</p>}
            {(history.data?.answers.length ?? 0) === 0 && !history.isLoading && (
              <p className="muted cg-fineprint">Noch nichts gefragt.</p>
            )}
            {history.data?.answers.map((record) => (
              <HistoryRow
                key={record.id}
                record={record}
                onRemove={async () => {
                  await api.codegraph.removeAnswer(projectId, record.id);
                  queryClient.invalidateQueries({ queryKey: ["codegraph", "answers", projectId] });
                }}
              />
            ))}
          </section>
        )}
      </div>
    </div>
  );
}

export function AnswerView({
  answer,
  onOpen,
  onShowOnMap,
  projectId,
  notesProjectId,
}: {
  answer: CodeAnswer;
  onOpen?: (path: string, line: number) => void;
  onShowOnMap?: (nodeId: string) => void;
  projectId: string;
  notesProjectId: string;
}) {
  const verdict = VERDICT_META[answer.verdict] ?? VERDICT_META.broken;
  const quotable = useMemo(
    // Nur was die Prüfung gehalten hat, darf in eine Notiz wandern. Ein Zitat,
    // das schon in der Antwort nicht belegt war, wäre in einer Notiz erst recht
    // keins — dort steht später niemand mehr daneben, der es einordnet.
    () => answer.citations.filter((citation) => citation.status === "verified"),
    [answer.citations],
  );
  return (
    <div className="cg-answer">
      <div className={`cg-verdict cg-verdict--${verdict.tone}`}>
        {verdict.tone === "ok" ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
        <span>{verdict.label}</span>
        <span className="cg-tabs-spacer" />
        <span className="cg-row-meta">
          {answer.tool_calls} Nachschlagevorgänge · {answer.model}
        </span>
      </div>

      {answer.tool_calling_fallback && (
        <p className="cg-fineprint muted">
          Dieser Anbieter kann keine Werkzeugaufrufe — die Antwort entstand allein aus dem vorab
          geholten Ausschnitt.
        </p>
      )}
      {answer.truncated && (
        <p className="cg-fineprint muted">
          Die Werkzeugschleife lief in ihr Rundenlimit; die Antwort ist aus dem Bisherigen gebaut.
        </p>
      )}
      {answer.bare_citations > 0 && (
        <p className="cg-fineprint cg-warn">
          {answer.bare_citations}× ein nummerierter Verweis wie <code>[1]</code> — das ist kein
          Beleg und zählt als Fehler.
        </p>
      )}

      <AnswerText text={answer.answer} citations={answer.citations} onOpen={onOpen} />

      {answer.quote_mismatches.length > 0 && (
        <div className="cg-notice cg-notice--error">
          <XCircle size={15} />
          <div>
            <strong>Wörtliches Zitat stimmt nicht mit der Datei überein</strong>
            {answer.quote_mismatches.map((mismatch) => (
              <p key={mismatch} className="cg-fineprint">
                {mismatch}
              </p>
            ))}
          </div>
        </div>
      )}

      {answer.trail.length > 0 && (
        <Trail steps={answer.trail} onOpen={onOpen} onShowOnMap={onShowOnMap} />
      )}

      {quotable.length > 0 && (
        <CiteIntoNote projectId={projectId} notesProjectId={notesProjectId} citations={quotable} />
      )}

      {answer.papers.length > 0 && (
        <section className="cg-section">
          <h4>
            <BookOpen size={13} /> Herangezogene Papers
          </h4>
          {answer.papers.map((paper) => (
            <div key={paper.paper_id} className="cg-row">
              <span className="cg-row-main">
                {answer.paper_citations.includes(paper.paper_id) && (
                  <span className="cg-cite cg-cite--verified" title="in der Antwort zitiert">
                    zitiert
                  </span>
                )}{" "}
                {paper.title || paper.paper_id}
              </span>
              <span className="cg-row-meta">[{paper.paper_id}]</span>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

/**
 * Der Antworttext mit den Belegstellen als Chips.
 *
 * Geschnitten wird über die Versätze aus der Prüfung — das Backend rechnet die
 * Byte-Versätze der Rust-Seite vorher in JS-Zeichenversätze um, sonst liefen die
 * Chips um jedes Umlaut-Byte nach links.
 */
export function AnswerText({
  text,
  citations,
  onOpen,
}: {
  text: string;
  citations: CodeCitation[];
  onOpen?: (path: string, line: number) => void;
}) {
  const parts = useMemo(() => {
    const ordered = [...citations].sort((a, b) => a.start - b.start);
    const chunks: Array<{ text: string; citation?: CodeCitation }> = [];
    let cursor = 0;
    for (const citation of ordered) {
      if (citation.start < cursor || citation.end > text.length) continue;
      if (citation.start > cursor) chunks.push({ text: text.slice(cursor, citation.start) });
      chunks.push({ text: text.slice(citation.start, citation.end), citation });
      cursor = citation.end;
    }
    if (cursor < text.length) chunks.push({ text: text.slice(cursor) });
    return chunks;
  }, [text, citations]);

  return (
    <p className="cg-answer-text">
      {parts.map((part, index) =>
        part.citation ? (
          <button
            key={index}
            className={`cg-cite cg-cite--${part.citation.status}`}
            title={CITATION_META[part.citation.status].hint}
            onClick={() => onOpen?.(part.citation!.path, part.citation!.from_line)}
          >
            {part.text}
            <em>{CITATION_META[part.citation.status].label}</em>
          </button>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </p>
  );
}

/**
 * Belegte Stellen in eine Notiz übernehmen.
 *
 * Das Zitat landet in `note_citations` neben den Paper-Zitaten — mit dem Hash
 * der Datei zum Zeitpunkt des Zitierens. Ändert sich die Datei später, wird das
 * Zitat als veraltet angezeigt, statt eine alte Zeilennummer als aktuell
 * auszugeben.
 */
function CiteIntoNote({
  projectId,
  notesProjectId,
  citations,
}: {
  projectId: string;
  notesProjectId: string;
  citations: CodeCitation[];
}) {
  const [noteId, setNoteId] = useState("");
  const [done, setDone] = useState<string | null>(null);

  const notes = useQuery({
    queryKey: ["notes", notesProjectId],
    queryFn: () => api.listNotes(notesProjectId),
  });

  const cite = useMutation({
    mutationFn: async () => {
      for (const citation of citations) {
        await api.codegraph.cite(projectId, {
          note_id: noteId,
          rel_path: citation.path,
          start_line: citation.from_line,
          end_line: citation.to_line,
        });
      }
    },
    onSuccess: () => setDone(`${citations.length} Belege übernommen`),
  });

  if ((notes.data?.items.length ?? 0) === 0) return null;

  return (
    <div className="cg-cite-into-note">
      <select value={noteId} onChange={(event) => setNoteId(event.target.value)}>
        <option value="">Beleg in Notiz übernehmen …</option>
        {notes.data!.items.map((note) => (
          <option key={note.id} value={note.id}>
            {note.title}
          </option>
        ))}
      </select>
      <button
        className="wk-btn"
        disabled={!noteId || cite.isPending}
        onClick={() => cite.mutate()}
        title={`${citations.length} belegte Stellen aus dieser Antwort`}
      >
        {cite.isPending ? "übernehme …" : `${citations.length} übernehmen`}
      </button>
      {done && <span className="cg-row-meta">{done}</span>}
    </div>
  );
}

/** Die Spur: der Weg durch den Code, den man selbst abgehen kann. */
function Trail({
  steps,
  onOpen,
  onShowOnMap,
}: {
  steps: CodeTrailStep[];
  onOpen?: (path: string, line: number) => void;
  onShowOnMap?: (nodeId: string) => void;
}) {
  return (
    <section className="cg-section cg-trail">
      <h4 title="Zwei bis sechs Schritte in der Reihenfolge, in der die Daten fließen.">
        Spur <span className="cg-count">{steps.length}</span>
      </h4>
      <ol>
        {steps.map((step, index) => (
          <li key={`${step.path}:${step.line}:${index}`} className={step.verified ? "" : "cg-trail--unverified"}>
            <button className="cg-row-link" onClick={() => onOpen?.(step.path, step.line)}>
              {step.path}:{step.line}
            </button>
            {/* Nur Schritte, die das Backend an ein Symbol binden konnte — ohne
                node_id gäbe es auf der Karte nichts anzuspringen. */}
            {onShowOnMap && step.node_id && (
              <button
                className="cg-row-link cgp-trail-map"
                title="Diesen Schritt auf der Karte zeigen"
                onClick={() => onShowOnMap(step.node_id!)}
              >
                auf der Karte
              </button>
            )}
            {!step.verified && (
              <span
                className="cg-cite cg-cite--not_retrieved"
                title="Dieser Schritt zeigt auf Code, den das Modell nie gesehen hat."
              >
                ungeprüft
              </span>
            )}
            <span className="cg-trail-reason">{step.reason}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function HistoryRow({ record, onRemove }: { record: CodeAnswerRecord; onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  const verdict = record.verdict ? VERDICT_META[record.verdict as CodeVerdict] : null;
  return (
    <div className="cg-row cg-row--symbol">
      <button className="cg-row-main" onClick={() => setOpen((value) => !value)}>
        {verdict && (
          <span className={`cg-cite cg-cite--${verdict.tone === "ok" ? "verified" : "not_retrieved"}`}>
            {verdict.tone === "ok" ? "belegt" : "prüfen"}
          </span>
        )}{" "}
        {record.question}
        {open && <span className="cg-doc">{record.answer}</span>}
      </button>
      <button className="wk-iconbtn" title="Eintrag löschen" onClick={onRemove}>
        <Trash2 size={13} />
      </button>
    </div>
  );
}

export default CodeAskPanel;

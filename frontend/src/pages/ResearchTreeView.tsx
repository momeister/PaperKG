// ResearchTreeView (Tiefenanalyse-Baum) — aus WorkspaceSubComponents.tsx extrahiert;
// dort re-exportiert (Konsumenten unveraendert). Superset-Importe der Quelldatei.
// Standalone, prop-driven sub-components extracted from WorkspacePage.tsx (they
// close over no parent state). Kept together because they reference each other
// (navigator uses PaneHeading/CollapsedPane).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ChangeEvent,
  ClipboardEvent as ReactClipboardEvent,
  DragEvent as ReactDragEvent,
  FormEvent,
  KeyboardEvent as ReactKeyboardEvent,
  MutableRefObject,
  PointerEvent as ReactPointerEvent,
  ReactNode
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Command,
  DownloadCloud,
  FilePlus2,
  FileSearch,
  FileText,
  FolderPlus,
  GitBranch,
  GitMerge,
  Globe,
  Link2,
  ListChecks,
  Database,
  FlaskConical,
  Loader2,
  Maximize2,
  MessageSquareText,
  Minimize2,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Pin,
  Plus,
  Quote,
  Search,
  Send,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Square,
  Star,
  Trash2,
  Upload,
  X,
  XCircle
} from "lucide-react";

import { api, streamResearchTree, streamAutoAnswer, exportResearchTree } from "../api";
import type { ResearchTreeRequest, ResearchTreeExportOptions } from "../api";
import { downloadBlob } from "../download";
import { colorVarsForPaperId, evidenceColorVars, isGreySourcePaperId } from "../citationColors";
import { EmptyState } from "../components/EmptyState";
import { GreySourceView } from "../components/GreySourceView";
import { PdfPane } from "../components/PdfPane";
import { Status } from "../components/Status";
import { noteProjectId, projectScopeLabel } from "../projectScope";
import { useAppState } from "../state";
import type {
  Answer,
  CitationLink,
  ClaimCheckResult,
  DeepResearchFinding,
  GreySource,
  NoteAiMessage,
  NoteAiThread,
  NoteCitation,
  Paper,
  ParallelSession,
  ResearchNode,
  VerificationEvidence,
  VerificationSource
} from "../types";
import { ParallelResearchPanel } from "./ParallelResearchPanel";
import { ParallelResultsTab } from "./ParallelResultsTab";
import {
  AnswerText,
  answerLimitFor,
  citationContext,
  citationMetasFor,
  citationSegmentFromParts,
  claimVerdictLabel,
  cleanAnswerQuote,
  EvidenceVerificationBadge,
  evidenceLocationUncertain,
  fetchAssistantSession,
  formatAnswerForNote,
  formatNoteQuote,
  formatNoteQuoteMulti,
  formatTurnTime,
  loadAssistantSession,
  meaningfulQuote,
  mergeVerification,
  noteCitation,
  sameCitation,
  saveAssistantSession,
  shortEvidenceText,
  turnBlocks,
  turnContext,
  verificationSourcesFor
} from "./AssistantPage";
import type { CitationInsertExtras, CitationMeta } from "./AssistantPage";
import { NotesSurface } from "./NotesPage";
import type { NotesSurfaceActions, NotesSurfaceSnapshot } from "./NotesPage";
import { AnalysisPanel } from "./AnalysisPanel";
import { DatasetsPanel } from "./DatasetsPanel";
import {
  ALL_PAPERS_SCOPES,
  EMPTY_NOTES_SNAPSHOT,
  MAX_PREVERIFY_CITATIONS,
  WORKSPACE_COMMANDS,
  activeScopePaperId,
  answerSuggestsWebSearch,
  citationPoolFor,
  classifyDroppedFile,
  classifyPastedText,
  extractInlineWebToken,
  fileExtension,
  findingToGreyRecord,
  findingToGreySource,
  latestThreadAnswer,
  loadWorkspaceBoolean,
  loadWorkspaceNumber,
  matchWorkspaceCommands,
  normalizeFilter,
  normalizeWorkspacePaper,
  noteCitationEvidence,
  restoredActiveTurnFor,
  sameNotesSnapshot,
  saveWorkspaceBoolean,
  saveWorkspaceNumber,
  shortSelectionPreview,
  sortPinnedThreads,
  stripThreadContext,
  threadCollapsed,
  threadMessages,
  threadPinned,
  workspacePaperId,
  workspacePaperTitle,
} from "./workspaceHelpers";
import type {
  WorkspaceNavigatorTab, WorkspacePdfTarget, WorkspaceCommandDef, WorkspaceActionEntry,
  WorkspacePaperRecord, PaperQuestionScope, WorkspaceAssistantMode, AssistantAnswerBlock,
  AssistantTurn, DroppedSourceKind,
} from "./WorkspacePage";

export function ResearchTreeView({
  nodes,
  loading,
  llmError,
  onStop,
  onResume,
  onCitationClick,
  onCitationInsert,
  onCitationInsertPreview,
  onCitationInsertPreviewClear,
  onDrillDeeper,
  onSaveToNotes,
}: {
  nodes: ResearchNode[];
  loading: boolean;
  llmError: { kind: string; message: string; error: string } | null;
  onStop: () => void;
  onResume: () => void;
  onCitationClick: (source: VerificationSource, evidenceIndex: number) => void;
  onCitationInsert: (source: VerificationSource, evidenceIndex: number, quote: string, extras?: CitationInsertExtras) => void;
  onCitationInsertPreview: (source: VerificationSource, evidenceIndex: number, quote: string, extras?: CitationInsertExtras) => void;
  onCitationInsertPreviewClear: () => void;
  onDrillDeeper: (nodeId: string, question: string) => void;
  onSaveToNotes: () => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [activeTab, setActiveTab] = useState<"tree" | "synthesis">("tree");
  const [exportOpen, setExportOpen] = useState(false);
  const [exportFormat, setExportFormat] = useState<"pdf" | "zip">("pdf");
  const [exportOpts, setExportOpts] = useState<ResearchTreeExportOptions>({
    tikz_tree: true,
    charts: true,
    tables: true,
    comfyui_images: false,
  });
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<{ kind: "error" | "warn" | "ok"; text: string } | null>(null);

  const treeNodes = nodes.filter((n) => n.status !== "synthesis");
  const synthesisNode = nodes.find((n) => n.status === "synthesis");

  // Auto-switch to synthesis tab when it arrives
  useEffect(() => {
    if (synthesisNode?.document) setActiveTab("synthesis");
  }, [synthesisNode?.document]);

  async function handleExport() {
    if (!synthesisNode?.document || exporting) return;
    setExporting(true);
    setExportMsg(null);
    const rootQuestion =
      treeNodes.find((n) => n.depth === 0)?.question ?? synthesisNode.question ?? "Tiefenanalyse";
    // De-dupe the verification sources across all nodes for the bibliography; the
    // backend enriches year/doi/url from each node's answer.sources.
    const seen = new Set<string>();
    const sources: VerificationSource[] = [];
    for (const n of treeNodes) {
      for (const s of n.verification ?? []) {
        if (!seen.has(s.paper_id)) {
          seen.add(s.paper_id);
          sources.push(s);
        }
      }
    }
    try {
      const result = await exportResearchTree({
        root_question: rootQuestion,
        document: synthesisNode.document,
        nodes,
        sources,
        format: exportFormat,
        options: exportOpts,
      });
      downloadBlob(result.filename, result.blob);
      if (result.warnings.length > 0) {
        setExportMsg({ kind: "warn", text: result.warnings.join(" ") });
      } else {
        setExportMsg({ kind: "ok", text: `Heruntergeladen: ${result.filename}` });
      }
    } catch (e) {
      setExportMsg({ kind: "error", text: e instanceof Error ? e.message : "Export fehlgeschlagen" });
    } finally {
      setExporting(false);
    }
  }

  const rootNodes = treeNodes.filter((n) => n.parent_id === null);
  const childrenOf = (id: string) => treeNodes.filter((n) => n.parent_id === id);

  function toggle(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function renderNode(node: ResearchNode): ReactNode {
    const children = childrenOf(node.id);
    const isCollapsed = collapsed.has(node.id);
    const hasChildren = children.length > 0 || (node.status === "done" && (node.child_count ?? 0) > 0);
    const verification = node.verification ?? [];
    // Fall back to answer.sources so a reloaded session (trimmed verification) still
    // resolves/colours citations instead of greying them all out.
    const citationPool = citationPoolFor(verification, node.answer);
    const isHarvesting = node.status === "harvesting";

    return (
      <div key={node.id} className="research-tree-node" style={{ marginLeft: node.depth > 0 ? `${node.depth * 20}px` : 0 }}>
        <div className="research-tree-node-header">
          {hasChildren ? (
            <button type="button" className="icon-button research-tree-toggle" onClick={() => toggle(node.id)}>
              {isCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
            </button>
          ) : (
            <span className="research-tree-toggle-spacer" />
          )}
          <span className="research-tree-node-depth">T{node.depth + 1}</span>
          <strong className="research-tree-question">{node.question}</strong>
          {(node.status === "running" || isHarvesting) ? <Loader2 size={13} className="spin" /> : null}
          {isHarvesting ? <span className="muted-row" style={{ fontSize: "11px" }}>Suche Quellen… (Paper + Web)</span> : null}
          {node.status === "error" ? <span className="warning-row" style={{ fontSize: "11px" }}>Fehler</span> : null}
          {node.status === "done" && !loading ? (
            <button
              type="button"
              className="icon-button"
              style={{ marginLeft: "auto", fontSize: "11px", display: "flex", alignItems: "center", gap: "3px", opacity: 0.7 }}
              onClick={() => onDrillDeeper(node.id, node.question)}
              title="Tiefer in diese Frage einsteigen"
            >
              <Plus size={11} />
              <span>Tiefer</span>
            </button>
          ) : null}
        </div>
        {!isCollapsed && node.answer ? (
          <div className="research-tree-answer">
            <AnswerText
              answer={node.answer.answer}
              getCitationMeta={(citation, context, start) =>
                citationMetasFor(citationPool, citation, context, node.answer?.citation_links ?? [], start)
              }
              onCitationClick={() => {}}
              onCitationMetaClick={(meta) => onCitationClick(meta.source, meta.evidenceIndex)}
              onCitationInsert={(source, evidenceIndex, quote, extras) => onCitationInsert(source, evidenceIndex, quote, extras)}
              onCitationInsertPreview={(source, evidenceIndex, quote, extras) => onCitationInsertPreview(source, evidenceIndex, quote, extras)}
              onCitationInsertPreviewClear={onCitationInsertPreviewClear}
            />
            {verification.length > 0 ? (
              <div className="research-tree-sources">
                {verification.map((src) => (
                  <button
                    key={src.paper_id}
                    type="button"
                    className="citation-link citation-link--mapped"
                    style={colorVarsForPaperId(src.paper_id, 0)}
                    onClick={() => onCitationClick(src, 0)}
                    title={src.title || src.paper_id}
                  >
                    {src.title ? src.title.slice(0, 40) + (src.title.length > 40 ? "…" : "") : src.paper_id}
                  </button>
                ))}
              </div>
            ) : null}
            {((node.harvested_papers?.length ?? 0) > 0 || (node.harvested_grey?.length ?? 0) > 0) ? (
              <div className="research-tree-harvested">
                <span className="research-tree-harvested-label">Neu gefunden:</span>
                {node.harvested_papers?.map((p) => (
                  <span key={p.id} className="research-tree-harvested-item research-tree-harvested-paper" title={p.id}>
                    {p.title.slice(0, 50) + (p.title.length > 50 ? "…" : "")}
                  </span>
                ))}
                {node.harvested_grey?.map((g) => (
                  <a key={g.id} href={g.url} target="_blank" rel="noopener noreferrer"
                    className="research-tree-harvested-item research-tree-harvested-web" title={g.url}>
                    {(g.title || g.url).slice(0, 50) + ((g.title || g.url).length > 50 ? "…" : "")}
                  </a>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
        {!isCollapsed ? children.map((child) => renderNode(child)) : null}
      </div>
    );
  }

  function renderSynthesis(doc: string): ReactNode {
    // Parse markdown into sections (heading + content) for anchor-based TOC
    type Section = { level: number; title: string; content: string };
    const sections: Section[] = [];
    let preamble = "";
    let currentSection: { level: number; title: string; lines: string[] } | null = null;
    for (const line of doc.split("\n")) {
      const h2 = line.match(/^## (.+)/);
      const h3 = line.match(/^### (.+)/);
      if (h2 || h3) {
        if (currentSection) {
          sections.push({ level: currentSection.level, title: currentSection.title, content: currentSection.lines.join("\n").trim() });
        }
        currentSection = { level: h2 ? 2 : 3, title: (h2?.[1] ?? h3?.[1] ?? ""), lines: [] };
      } else {
        if (currentSection) currentSection.lines.push(line);
        else preamble += line + "\n";
      }
    }
    if (currentSection) sections.push({ level: currentSection.level, title: currentSection.title, content: currentSection.lines.join("\n").trim() });
    preamble = preamble.trim();

    // Strip basic markdown syntax that AnswerText renders as literals (bold, bullets).
    // Citation brackets [arxiv:...] are preserved for chip rendering.
    function stripMd(text: string): string {
      return text
        .replace(/\*\*(.+?)\*\*/g, "$1")  // **bold** → bold
        .replace(/\*(.+?)\*/g, "$1")       // *italic* → italic
        .replace(/^- /gm, "• ")           // - bullet → • bullet
        .replace(/^\d+\. /gm, "");        // 1. item → item
    }

    // Aggregate verification sources and citation links from all done nodes so
    // citations in the synthesis document resolve to real paper evidence. Falls back
    // to answer.sources (always persisted) so a reloaded session keeps its citations
    // coloured instead of greying every one out as unresolved "!".
    const synthVerification: VerificationSource[] = [];
    const synthCitationLinks: CitationLink[] = [];
    const seenPaperIds = new Set<string>();
    for (const node of treeNodes) {
      for (const src of citationPoolFor(node.verification, node.answer)) {
        if (!seenPaperIds.has(src.paper_id)) {
          seenPaperIds.add(src.paper_id);
          synthVerification.push(src);
        }
      }
      if (node.answer?.citation_links) {
        synthCitationLinks.push(...node.answer.citation_links);
      }
    }

    const sharedAnswerProps = {
      getCitationMeta: (citation: string, context?: string, citationStart?: number) =>
        citationMetasFor(synthVerification, citation, context, synthCitationLinks, citationStart),
      onCitationClick: () => {},
      onCitationMetaClick: (meta: CitationMeta) => onCitationClick(meta.source, meta.evidenceIndex),
      onCitationInsert: (source: VerificationSource, evidenceIndex: number, quote: string, extras?: CitationInsertExtras) =>
        onCitationInsert(source, evidenceIndex, quote, extras),
      onCitationInsertPreview: (source: VerificationSource, evidenceIndex: number, quote: string, extras?: CitationInsertExtras) =>
        onCitationInsertPreview(source, evidenceIndex, quote, extras),
      onCitationInsertPreviewClear,
    };

    // Render a section's content, turning `####` lines into <h4> subheadings instead of
    // leaving the literal hashes in the prose (sections are split on ##/### above, so only
    // h4+ reaches here).
    function renderSectionContent(content: string, keyPrefix: string): ReactNode {
      const blocks: ReactNode[] = [];
      let buffer: string[] = [];
      let counter = 0;
      const flush = () => {
        const text = buffer.join("\n").trim();
        buffer = [];
        if (text) blocks.push(<AnswerText key={`${keyPrefix}-t${counter++}`} answer={stripMd(text)} {...sharedAnswerProps} />);
      };
      for (const line of content.split("\n")) {
        const h4 = line.match(/^#{4,6}\s+(.+)/);
        if (h4) {
          flush();
          blocks.push(<h4 key={`${keyPrefix}-h${counter++}`} className="research-synthesis-h4">{stripMd(h4[1].trim())}</h4>);
        } else {
          buffer.push(line);
        }
      }
      flush();
      return blocks;
    }

    return (
      <div className="research-synthesis-panel">
        {sections.length > 0 ? (
          <nav className="research-synthesis-toc">
            <strong>Inhalt</strong>
            {sections.map((s, i) => (
              <a
                key={i}
                href={`#synth-sec-${i}`}
                className={`research-synthesis-toc-item research-synthesis-toc-level-${s.level}`}
                onClick={(e) => {
                  e.preventDefault();
                  document.getElementById(`synth-sec-${i}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
                }}
              >
                {s.title}
              </a>
            ))}
            {synthVerification.length > 0 ? (
              <a
                href="#synth-sec-bibliography"
                className="research-synthesis-toc-item research-synthesis-toc-level-2"
                onClick={(e) => {
                  e.preventDefault();
                  document.getElementById("synth-sec-bibliography")?.scrollIntoView({ behavior: "smooth", block: "start" });
                }}
              >
                Quellenverzeichnis
              </a>
            ) : null}
          </nav>
        ) : null}
        <div className="research-synthesis-body">
          {preamble ? renderSectionContent(preamble, "synth-pre") : null}
          {sections.map((s, i) => (
            <div key={i} id={`synth-sec-${i}`} className="research-synthesis-section">
              {s.level === 2 ? <h2 className="research-synthesis-h2">{s.title}</h2> : <h3 className="research-synthesis-h3">{s.title}</h3>}
              {s.content ? renderSectionContent(s.content, `synth-sec-${i}`) : null}
            </div>
          ))}
          {synthVerification.length > 0 ? (
            <div id="synth-sec-bibliography" className="research-synthesis-section">
              <h2 className="research-synthesis-h2">Quellenverzeichnis</h2>
              <ol className="research-synthesis-bibliography">
                {synthVerification.map((src) => (
                  <li key={src.paper_id}>
                    <button
                      type="button"
                      className="citation-link citation-link--mapped"
                      style={colorVarsForPaperId(src.paper_id, 0)}
                      onClick={() => onCitationClick(src, 0)}
                      title={src.paper_id}
                    >
                      {src.title || src.paper_id}
                    </button>
                    <span className="muted-row" style={{ marginLeft: 6, fontSize: "11px" }}>
                      {src.paper_id}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  const doneCount = treeNodes.filter((n) => n.status === "done").length;

  return (
    <div className="research-tree-panel">
      <div className="research-tree-header">
        <GitBranch size={15} />
        <strong>Tiefenanalyse</strong>
        <div className="segmented research-tree-tabs" style={{ marginLeft: "8px" }}>
          <button
            type="button"
            className={activeTab === "tree" ? "active" : ""}
            onClick={() => setActiveTab("tree")}
          >
            Baumansicht
          </button>
          <button
            type="button"
            className={activeTab === "synthesis" ? "active" : ""}
            disabled={!synthesisNode}
            onClick={() => setActiveTab("synthesis")}
          >
            Gesamtantwort
          </button>
        </div>
        <span className="muted-row" style={{ flex: 1, fontSize: "12px" }}>
          {loading ? `${treeNodes.length} Antworten erhalten…` : `${treeNodes.length} Antworten`}
        </span>
        {!loading && doneCount > 0 ? (
          <button type="button" className="icon-button" style={{ fontSize: "11px", display: "flex", alignItems: "center", gap: "3px" }} onClick={onSaveToNotes} title="Gesamten Baum in aktive Notiz speichern">
            <NotebookPen size={12} />
            <span>In Notiz</span>
          </button>
        ) : null}
        {synthesisNode?.document ? (
          <div style={{ position: "relative" }}>
            <button
              type="button"
              className="icon-button"
              style={{ fontSize: "11px", display: "flex", alignItems: "center", gap: "3px" }}
              onClick={() => { setExportOpen((o) => !o); setExportMsg(null); }}
              title="Gesamtantwort als LaTeX-PDF oder .tex/.zip exportieren"
            >
              <DownloadCloud size={12} />
              <span>PDF/LaTeX</span>
            </button>
            {exportOpen ? (
              <div
                role="dialog"
                style={{
                  position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 50, width: 272,
                  background: "var(--surface, #ffffff)", border: "1px solid var(--border, #d4d4d4)",
                  borderRadius: 8, boxShadow: "0 6px 24px rgba(0,0,0,0.18)", padding: 12,
                  display: "flex", flexDirection: "column", gap: 8, fontSize: 12,
                  color: "var(--text, #222)", cursor: "default",
                }}
              >
                <strong style={{ fontSize: 12 }}>Als Dokument exportieren</strong>
                <div className="segmented" style={{ display: "flex" }}>
                  <button type="button" className={exportFormat === "pdf" ? "active" : ""} style={{ flex: 1 }} onClick={() => setExportFormat("pdf")}>PDF</button>
                  <button type="button" className={exportFormat === "zip" ? "active" : ""} style={{ flex: 1 }} onClick={() => setExportFormat("zip")}>LaTeX (.zip)</button>
                </div>
                {([
                  ["tikz_tree", "Forschungsbaum (TikZ)"],
                  ["charts", "Statistik-Diagramme"],
                  ["tables", "Tabellen"],
                  ["comfyui_images", "KI-Bilder (ComfyUI)"],
                ] as [keyof ResearchTreeExportOptions, string][]).map(([key, label]) => (
                  <label key={key} style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={exportOpts[key]}
                      onChange={(e) => setExportOpts((o) => ({ ...o, [key]: e.target.checked }))}
                    />
                    {label}
                  </label>
                ))}
                {exportOpts.comfyui_images ? (
                  <span className="muted-row" style={{ fontSize: 10 }}>
                    ComfyUI muss lokal laufen (Port 8188), sonst wird dieser Schritt übersprungen.
                  </span>
                ) : null}
                <button
                  type="button"
                  className="button button-compact"
                  disabled={exporting}
                  onClick={handleExport}
                  style={{ justifyContent: "center" }}
                >
                  {exporting ? (<><Loader2 size={12} className="spin" /> Erzeuge…</>) : (<><FileText size={12} /> Erstellen</>)}
                </button>
                {exportMsg ? (
                  <span style={{
                    fontSize: 11,
                    color: exportMsg.kind === "error" ? "var(--danger, #b00020)"
                      : exportMsg.kind === "warn" ? "var(--warning, #a85d00)"
                      : "var(--success, #1a7f37)",
                  }}>
                    {exportMsg.text}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
        {loading && !synthesisNode ? (
          <button type="button" className="button button-compact" onClick={onStop} title="Analyse stoppen">
            <Square size={12} />
            <span>Stopp</span>
          </button>
        ) : null}
        {!loading && !synthesisNode && nodes.some((n) => n.status === "done") ? (
          <button type="button" className="button button-compact" onClick={onResume} title="Analyse fortsetzen (bereits beantwortete Knoten werden übersprungen)">
            <GitBranch size={12} />
            <span>Fortsetzen</span>
          </button>
        ) : null}
        {loading && synthesisNode ? (
          <span
            className="muted-row"
            style={{ fontSize: "11px", display: "flex", alignItems: "center", gap: "4px" }}
            title="Die Gesamtantwort wird Abschnitt für Abschnitt aus den Teilantworten geschrieben."
          >
            <Loader2 size={12} className="spin" />
            {(() => {
              const written = (synthesisNode.document?.match(/^#{2,3}\s/gm) ?? []).length;
              return written > 0 ? `Synthese… (Abschnitt ${written})` : "Synthese…";
            })()}
          </span>
        ) : null}
      </div>
      {llmError ? (
        <div className={`research-tree-llm-error research-tree-llm-error--${llmError.kind}`} role="alert">
          <AlertTriangle size={15} />
          <div className="research-tree-llm-error-body">
            <strong>
              {llmError.kind === "quota"
                ? "KI-Kontingent erschöpft"
                : llmError.kind === "rate_limit"
                  ? "KI-Rate-Limit erreicht"
                  : llmError.kind === "auth"
                    ? "KI-Authentifizierung fehlgeschlagen"
                    : llmError.kind === "connection"
                      ? "Keine Verbindung zum KI-Modell"
                      : "KI-Anfrage fehlgeschlagen"}
            </strong>
            <span>{llmError.message}</span>
            <span className="research-tree-llm-error-detail">
              Die unten gezeigten Antworten sind nur evidenzbasiert (ohne KI-Synthese) und der Baum
              ist evtl. nicht vollständig verzweigt.
              {llmError.error ? ` Details: ${llmError.error.slice(0, 200)}` : ""}
            </span>
          </div>
        </div>
      ) : null}
      {activeTab === "tree" ? (
        <div className="research-tree-body">
          {rootNodes.length ? rootNodes.map((n) => renderNode(n)) : (
            loading ? <div className="muted-row"><Loader2 size={14} className="spin" /> Zerlege Frage…</div> : null
          )}
        </div>
      ) : (
        synthesisNode?.document ? renderSynthesis(synthesisNode.document) : (
          <div className="muted-row" style={{ padding: "16px" }}>
            <Loader2 size={14} className="spin" /> Gesamtantwort wird generiert…
          </div>
        )
      )}
    </div>
  );
}

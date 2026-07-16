import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Briefcase, FileSearch, Import as ImportIcon } from "lucide-react";

import { api } from "../api";
import { m } from "../motion";
import { useAppState } from "../state";

export type ResearchStage = "projekte" | "import" | "extraktion";

/* Die Pipeline ist das Herzstück des Forschung-Hubs: die drei realen
 * Arbeitsstufen (Projekt wählen → Quellen holen → Wissen extrahieren) als
 * verbundener Graph mit Live-Zählern. Die Zähler kommen aus denselben
 * Query-Caches, die die Stufen selbst benutzen — kein doppelter Fetch-Pfad.
 *
 * Achtung (Playwright): die Import-/Extraktion-Knoten tragen die aria-Labels
 * "Stufe Import"/"Stufe Extraktion" (werden im E2E geklickt). Der Projekte-
 * Knoten darf KEIN aria-label bekommen — jedes Label mit "Projekt" würde mit
 * getByLabel("Projekt") (Topbar-Select) kollidieren. */

function PipelineEdge({ active }: { active: boolean }) {
  return (
    <svg className={`pipeline-edge ${active ? "pipeline-edge--active" : ""}`} viewBox="0 0 64 12" preserveAspectRatio="none" aria-hidden="true">
      <line className="pipeline-edge-base" x1="1" y1="6" x2="63" y2="6" />
      <line className="pipeline-edge-flow" x1="1" y1="6" x2="63" y2="6" />
    </svg>
  );
}

function PipelineNode({
  active,
  ariaLabel,
  icon,
  label,
  value,
  hint,
  onClick
}: {
  active: boolean;
  ariaLabel?: string;
  icon: ReactNode;
  label: string;
  value: string;
  hint: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`pipeline-node pressable ${active ? "pipeline-node--active" : ""}`}
      aria-label={ariaLabel}
      aria-current={active ? "step" : undefined}
      onClick={onClick}
    >
      {active ? <m.span className="pipeline-node-ring" layoutId="pipeline-active-ring" aria-hidden="true" /> : null}
      <span className="pipeline-node-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="pipeline-node-body">
        <span className="pipeline-node-label">{label}</span>
        {/* Zähler/Hinweis sind visuelle Zusatzinfo: aria-hidden hält den
            Accessible Name des Knotens stabil ("Projekte" bzw. das
            Stufen-Label) — sonst kollidiert z.B. ein Projektname im Hinweis
            mit Playwright-Locatorn, die Projekt-Buttons suchen. */}
        <span className="pipeline-node-value" aria-hidden="true">
          {value}
        </span>
        <span className="pipeline-node-hint" aria-hidden="true">
          {hint}
        </span>
      </span>
    </button>
  );
}

export function PipelineGraph({ stage, onStageSelect }: { stage: ResearchStage; onStageSelect: (stage: ResearchStage) => void }) {
  const { activeProject } = useAppState();
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.getProjects });
  const papersQuery = useQuery({
    queryKey: ["papers-count", activeProject],
    queryFn: () => api.listPapers({ project_id: activeProject, limit: 1 })
  });
  // Gleicher Key + gleiche Parameter wie die Extraktions-Stufe → geteilter Cache.
  const extractionQuery = useQuery({
    queryKey: ["extraction-library", "", activeProject ?? ""],
    queryFn: () => api.getExtractionLibrary("", activeProject)
  });

  const projects = projectsQuery.data?.projects ?? [];
  const activeProjectName = projects.find((project) => project.id === activeProject)?.name;
  const paperTotal = papersQuery.data?.total;

  const extractionItems = extractionQuery.data?.items ?? [];
  const pdfItems = extractionItems.filter((item) => item.source_type !== "grey" && item.pdf_path && item.pdf_available !== false);
  const extractedCount = pdfItems.filter((item) => item.latest_extraction_status === "success").length;

  return (
    <div className="pipeline-graph" role="group" aria-label="Forschungs-Pipeline">
      <PipelineNode
        active={stage === "projekte"}
        icon={<Briefcase size={18} />}
        label="Projekte"
        value={projectsQuery.isPending ? "…" : String(projects.length)}
        hint={activeProjectName ?? "Alle Papers"}
        onClick={() => onStageSelect("projekte")}
      />
      <PipelineEdge active={stage !== "projekte"} />
      <PipelineNode
        active={stage === "import"}
        ariaLabel="Stufe Import"
        icon={<ImportIcon size={18} />}
        label="Import"
        value={papersQuery.isPending || paperTotal === undefined ? "…" : String(paperTotal)}
        hint="Papers in der Bibliothek"
        onClick={() => onStageSelect("import")}
      />
      <PipelineEdge active={stage === "extraktion"} />
      <PipelineNode
        active={stage === "extraktion"}
        ariaLabel="Stufe Extraktion"
        icon={<FileSearch size={18} />}
        label="Extraktion"
        value={extractionQuery.isPending ? "…" : `${extractedCount}/${pdfItems.length}`}
        hint="PDFs mit Ergebnis"
        onClick={() => onStageSelect("extraktion")}
      />
    </div>
  );
}

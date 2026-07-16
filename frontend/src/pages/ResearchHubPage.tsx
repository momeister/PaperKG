import { lazy, Suspense } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { PipelineGraph } from "../components/PipelineGraph";
import type { ResearchStage } from "../components/PipelineGraph";
import { PageEnter } from "../motion";

// Die Stufen bleiben eigenständige, code-gesplittete Seiten (volle Logik in
// ProjectsPage/ImportPage/ExtractionPage) und werden hier nur eingebettet.
const ProjectsPage = lazy(() => import("./ProjectsPage").then((m) => ({ default: m.ProjectsPage })));
const ImportPage = lazy(() => import("./ImportPage").then((m) => ({ default: m.ImportPage })));
const ExtractionPage = lazy(() => import("./ExtractionPage").then((m) => ({ default: m.ExtractionPage })));

const STAGES: readonly ResearchStage[] = ["projekte", "import", "extraktion"];

function parseStage(value: string | undefined): ResearchStage {
  return STAGES.includes(value as ResearchStage) ? (value as ResearchStage) : "projekte";
}

/** Forschung-Hub: Projekt wählen → Import → Extraktion als verbundene
 *  Pipeline. Kein eigenes h1 — die Überschrift liefert die aktive Stufe
 *  (hält u.a. die Playwright-Heading-Assertions eindeutig). */
export function ResearchHubPage() {
  const params = useParams<{ stage?: string }>();
  const navigate = useNavigate();
  const stage = parseStage(params.stage);

  return (
    <section className="page research-hub">
      <PipelineGraph stage={stage} onStageSelect={(next) => navigate(`/forschung/${next}`)} />
      <Suspense fallback={<div className="page-loading">Lade…</div>}>
        <PageEnter key={stage} className="research-stage">
          {stage === "projekte" ? <ProjectsPage embedded /> : null}
          {stage === "import" ? <ImportPage embedded /> : null}
          {stage === "extraktion" ? <ExtractionPage embedded /> : null}
        </PageEnter>
      </Suspense>
    </section>
  );
}

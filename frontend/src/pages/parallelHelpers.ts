import type { ParallelSession, ParallelStage, ParallelVariant } from "../types";

// Pure helpers for the Parallel-Research Etappen UI (no JSX, vitest-friendly).

export const STAGE_STATUS_LABEL: Record<string, string> = {
  offen: "Offen",
  aktiv: "Aktiv",
  abgeschlossen: "Abgeschlossen",
};

export type StageGroup = {
  /** null ⇒ orphan group (legacy sessions without stages / unassigned variants). */
  stage: ParallelStage | null;
  variants: ParallelVariant[];
};

/** Group a session's variants by Etappe (stage order preserved). Variants without a
 * resolvable stage_id land in a trailing orphan group — for legacy sessions without
 * stages that group *is* the session, labelled "Etappe 1" via `stageGroupLabel`. */
export function groupVariantsByStage(session: ParallelSession): StageGroup[] {
  const stages = session.stages ?? [];
  const groups: StageGroup[] = stages.map((stage) => ({ stage, variants: [] }));
  const byId = new Map(groups.map((group) => [group.stage!.id, group]));
  const orphans: ParallelVariant[] = [];
  for (const variant of session.variants) {
    const group = variant.stage_id ? byId.get(variant.stage_id) : undefined;
    if (group) group.variants.push(variant);
    else orphans.push(variant);
  }
  if (orphans.length) groups.push({ stage: null, variants: orphans });
  return groups;
}

export function stageGroupLabel(group: StageGroup, index: number): string {
  if (group.stage) return group.stage.name || `Etappe ${index + 1}`;
  return index === 0 ? "Etappe 1" : "Weitere Varianten";
}

const VARIANT_STATUS_LABEL: Record<string, string> = {
  vorgeschlagen: "Vorgeschlagen",
  in_arbeit: "In Arbeit",
  ergebnis: "Ergebnis da",
  verworfen: "Verworfen",
};

function fmtDate(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

/** Full-session Markdown export: question, overview, per-Etappe variants + results +
 * professor reviews, follow-ups and the final End-Review. Keeps `[arxiv:...]` tokens
 * verbatim so the export stays source-traceable. */
export function buildParallelSessionMarkdown(session: ParallelSession): string {
  const lines: string[] = [];
  lines.push(`# Parallel-Recherche: ${session.question}`);
  const stageCount = (session.stages ?? []).length;
  const meta = [
    `Status: ${session.status}`,
    `${session.variants.length} Varianten`,
    stageCount ? `${stageCount} Etappen` : "",
    fmtDate(session.updated_timestamp) ? `Stand: ${fmtDate(session.updated_timestamp)}` : "",
  ].filter(Boolean);
  lines.push(`_${meta.join(" · ")}_`);

  if (session.overview_markdown?.trim()) {
    lines.push("", "## Überblick", "", session.overview_markdown.trim());
  }

  const groups = groupVariantsByStage(session);
  groups.forEach((group, index) => {
    const label = stageGroupLabel(group, index);
    const status = group.stage ? STAGE_STATUS_LABEL[group.stage.status] ?? group.stage.status : "";
    lines.push("", `## Etappe ${index + 1}: ${label}${status ? ` — ${status}` : ""}`);
    if (group.stage?.goal?.trim()) lines.push("", `Ziel: ${group.stage.goal.trim()}`);

    for (const variant of group.variants) {
      const vStatus = VARIANT_STATUS_LABEL[variant.status] ?? variant.status;
      lines.push("", `### Variante: ${variant.name} (${vStatus})`);
      if (variant.approach?.trim()) lines.push("", `Ansatz: ${variant.approach.trim()}`);
      if (variant.rationale?.trim()) lines.push("", `Warum: ${variant.rationale.trim()}`);
      if (variant.suggested_prompt?.trim()) {
        lines.push("", "Prompt:", "", "```", variant.suggested_prompt.trim(), "```");
      }
      for (const entry of variant.entries) {
        if (entry.role === "user" && entry.content.trim()) {
          lines.push("", "**Ergebnis:**", "");
          lines.push(...entry.content.trim().split("\n").map((line) => `> ${line}`));
        } else if (entry.role === "assistant") {
          const text = (entry.answer_payload?.answer || entry.content || "").trim();
          if (text) lines.push("", "**Professor-Review:**", "", text);
        }
      }
    }
    if (group.stage?.review_markdown?.trim()) {
      lines.push("", `### Etappen-Review: ${label}`, "", group.stage.review_markdown.trim());
    }
  });

  const followups = session.followups ?? [];
  if (followups.length) {
    lines.push("", "## Folgefragen");
    for (const followup of followups) {
      lines.push("", `### ${followup.question}`);
      const text = (followup.answer_payload?.answer || "").trim();
      if (text) lines.push("", text);
    }
  }

  if (session.synthesis_markdown?.trim()) {
    lines.push("", "## End-Review", "", session.synthesis_markdown.trim());
  }
  return lines.join("\n").trim() + "\n";
}

import type { CSSProperties } from "react";

type EvidenceColorStyle = CSSProperties & {
  "--evidence-color": string;
  "--evidence-soft": string;
  "--evidence-border": string;
};

const EVIDENCE_COLORS = [
  "#2563eb",
  "#d97706",
  "#059669",
  "#7c3aed",
  "#dc2626",
  "#0891b2",
  "#be185d",
  "#4d7c0f"
];

const EVIDENCE_SOFT_COLORS = [
  "rgba(37, 99, 235, 0.2)",
  "rgba(217, 119, 6, 0.22)",
  "rgba(5, 150, 105, 0.2)",
  "rgba(124, 58, 237, 0.2)",
  "rgba(220, 38, 38, 0.18)",
  "rgba(8, 145, 178, 0.2)",
  "rgba(190, 24, 93, 0.18)",
  "rgba(77, 124, 15, 0.2)"
];

const EVIDENCE_BORDER_COLORS = [
  "rgba(37, 99, 235, 0.48)",
  "rgba(217, 119, 6, 0.5)",
  "rgba(5, 150, 105, 0.46)",
  "rgba(124, 58, 237, 0.46)",
  "rgba(220, 38, 38, 0.42)",
  "rgba(8, 145, 178, 0.46)",
  "rgba(190, 24, 93, 0.42)",
  "rgba(77, 124, 15, 0.46)"
];

export function evidenceColorVars(index: number): EvidenceColorStyle {
  const colorIndex = Math.abs(index) % EVIDENCE_COLORS.length;
  return {
    "--evidence-color": EVIDENCE_COLORS[colorIndex],
    "--evidence-soft": EVIDENCE_SOFT_COLORS[colorIndex],
    "--evidence-border": EVIDENCE_BORDER_COLORS[colorIndex]
  };
}

// Muted/desaturated variants for grey sources (deep research)
const GREY_EVIDENCE_COLORS = ["#6b7fa8", "#9e8060", "#5a8a78", "#7a6ea0", "#906060", "#5a8498", "#8e6880", "#6e8260"];
const GREY_SOFT_COLORS = [
  "rgba(107,127,168,0.18)",
  "rgba(158,128,96,0.20)",
  "rgba(90,138,120,0.18)",
  "rgba(122,110,160,0.18)",
  "rgba(144,96,96,0.17)",
  "rgba(90,132,152,0.18)",
  "rgba(142,104,128,0.17)",
  "rgba(110,130,96,0.18)"
];
const GREY_BORDER_COLORS = [
  "rgba(107,127,168,0.40)",
  "rgba(158,128,96,0.42)",
  "rgba(90,138,120,0.38)",
  "rgba(122,110,160,0.38)",
  "rgba(144,96,96,0.36)",
  "rgba(90,132,152,0.38)",
  "rgba(142,104,128,0.36)",
  "rgba(110,130,96,0.38)"
];

export function greyEvidenceColorVars(index: number): EvidenceColorStyle {
  const colorIndex = Math.abs(index) % GREY_EVIDENCE_COLORS.length;
  return {
    "--evidence-color": GREY_EVIDENCE_COLORS[colorIndex],
    "--evidence-soft": GREY_SOFT_COLORS[colorIndex],
    "--evidence-border": GREY_BORDER_COLORS[colorIndex]
  };
}

export function isGreySourcePaperId(paperId: string | null | undefined): boolean {
  return Boolean(paperId && paperId.startsWith("grey::"));
}

function _stableColorIndex(paperId: string): number {
  let h = 0;
  for (let i = 0; i < paperId.length; i++) {
    h = (Math.imul(31, h) + paperId.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

// Picks the muted grey palette for grey::-prefixed sources, the vivid palette otherwise.
// Color is derived from the paper_id hash so the same source always gets the same
// color across all answer chips and evidence dock items, regardless of evidence position.
export function colorVarsForPaperId(paperId: string | null | undefined, index: number): EvidenceColorStyle {
  const colorIndex = paperId ? _stableColorIndex(paperId) : index;
  return isGreySourcePaperId(paperId) ? greyEvidenceColorVars(colorIndex) : evidenceColorVars(colorIndex);
}

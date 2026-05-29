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

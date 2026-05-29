import type { ReactNode } from "react";

type MetricCardProps = {
  label: string;
  value: ReactNode;
  tone?: "blue" | "green" | "amber" | "red" | "neutral";
  detail?: ReactNode;
};

export function MetricCard({ label, value, tone = "neutral", detail }: MetricCardProps) {
  return (
    <section className={`metric-card metric-card--${tone}`} aria-label={label}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </section>
  );
}

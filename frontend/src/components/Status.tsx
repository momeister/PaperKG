type StatusProps = {
  value?: string | boolean | null;
};

export function Status({ value }: StatusProps) {
  const normalized = String(value ?? "unknown").toLowerCase();
  const tone =
    normalized === "ok" || normalized === "success" || normalized === "completed" || normalized === "true"
      ? "good"
      : normalized === "warning" || normalized === "pending" || normalized === "running"
        ? "warn"
        : normalized === "failed" || normalized === "error" || normalized === "false"
          ? "bad"
          : "idle";
  return <span className={`status status--${tone}`}>{String(value ?? "unknown")}</span>;
}

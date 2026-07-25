/**
 * Gegenstueck zu `query/llm_errors.py` im Frontend.
 *
 * Das Backend stellt Fehlermeldungen ihre Art voran (`"[llm:quota] …"`) und
 * transportiert sie so durch `batch_job_items.error_message` und
 * `extraction_results.error_message`, ohne dass das DuckDB-Schema eine weitere
 * Spalte braucht. Hier wird das Praefix wieder abgetrennt und in eine
 * Ueberschrift uebersetzt — damit „KI-Kontingent erschöpft" als solches
 * dasteht statt als roher Fehlerstring irgendwo im Log.
 */

export type LlmErrorKind =
  | "quota"
  | "rate_limit"
  | "auth"
  | "context_length"
  | "connection"
  | "empty"
  | "unknown";

const KIND_PREFIX = "[llm:";

const HEADLINES: Record<LlmErrorKind, string> = {
  quota: "KI-Kontingent erschöpft",
  rate_limit: "Rate-Limit des KI-Anbieters erreicht",
  auth: "KI-Authentifizierung fehlgeschlagen",
  context_length: "Anfrage überschreitet das Kontextfenster",
  connection: "Keine Verbindung zum KI-Anbieter",
  empty: "Das Modell lieferte eine leere Antwort",
  unknown: "KI-Aufruf fehlgeschlagen"
};

export type ParsedLlmError = { kind: LlmErrorKind | null; message: string };

export function parseLlmError(raw: string | null | undefined): ParsedLlmError {
  const text = (raw ?? "").trim();
  if (!text.startsWith(KIND_PREFIX)) {
    return { kind: null, message: text };
  }
  const closing = text.indexOf("]");
  if (closing === -1) {
    return { kind: null, message: text };
  }
  const kind = text.slice(KIND_PREFIX.length, closing).trim() as LlmErrorKind;
  return {
    kind: kind in HEADLINES ? kind : "unknown",
    message: text.slice(closing + 1).trim()
  };
}

export function llmErrorHeadline(kind: LlmErrorKind): string {
  return HEADLINES[kind] ?? HEADLINES.unknown;
}

/**
 * Ist das ein Anbieter-Limit, bei dem jeder weitere Aufruf ebenfalls scheitert?
 * Dann stoppt das Backend den Batch — die UI sagt das entsprechend deutlich.
 */
export function isProviderLimit(kind: LlmErrorKind | null): boolean {
  return kind === "quota" || kind === "rate_limit" || kind === "auth";
}

/** Erste erkennbare LLM-Fehlerart aus einer Liste von Meldungen. */
export function firstLlmError(messages: Array<string | null | undefined>): ParsedLlmError | null {
  for (const message of messages) {
    const parsed = parseLlmError(message);
    if (parsed.kind) {
      return parsed;
    }
  }
  return null;
}

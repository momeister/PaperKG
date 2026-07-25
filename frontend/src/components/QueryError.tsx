import { AlertTriangle, RefreshCw } from "lucide-react";
import { ApiError } from "../api";

/**
 * Sichtbare Fehlermeldung fuer fehlgeschlagene Queries.
 *
 * Hintergrund: Bisher las jede Seite ihre Daten als `query.data?.items ?? []`.
 * Ein HTTP-500 wurde dadurch zu einer leeren Liste — im schlimmsten Fall zu einem
 * freundlichen "Keine Papers". Als ein zweiter Prozess den DuckDB-Lock hielt, sah
 * das aus, als waere die komplette Bibliothek geloescht worden. Fehler muessen als
 * Fehler erkennbar sein, nicht als Leere.
 */

export function formatQueryError(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (detail && typeof detail === "object" && "message" in detail) {
      return String((detail as { message: unknown }).message);
    }
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error ?? "Unbekannter Fehler");
}

/** Ergaenzt technische Fehler um einen konkreten naechsten Schritt. */
function hintFor(error: unknown): string | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  if (error.status === 0) {
    return "Läuft das Backend? Starte es mit `python scripts/run_product.py` oder `docker compose up`.";
  }
  if (error.status === 503) {
    // Der 503 kommt vom MetadataDBLockedError-Handler und erklärt sich selbst.
    return null;
  }
  if (error.status === 401) {
    return "SCIENCEKG_API_TOKEN ist gesetzt — das Frontend muss denselben Token senden.";
  }
  return null;
}

type QueryErrorProps = {
  error: unknown;
  /** Titelzeile, z.B. „Bibliothek konnte nicht geladen werden". */
  title?: string;
  /** Wird als „Erneut versuchen"-Knopf angeboten, üblicherweise `query.refetch`. */
  onRetry?: () => void;
};

export function QueryError({ error, title = "Laden fehlgeschlagen", onRetry }: QueryErrorProps) {
  if (!error) {
    return null;
  }
  const hint = hintFor(error);
  return (
    <div className="query-error" role="alert">
      <AlertTriangle size={18} className="query-error-icon" />
      <div className="query-error-body">
        <strong>{title}</strong>
        <span>{formatQueryError(error)}</span>
        {hint ? <span className="query-error-hint">{hint}</span> : null}
      </div>
      {onRetry ? (
        <button className="button button-compact" type="button" onClick={onRetry}>
          <RefreshCw size={14} />
          <span>Erneut versuchen</span>
        </button>
      ) : null}
    </div>
  );
}

/**
 * Hinweisstreifen fuer *teilweise* geladene Daten (`degraded` in der Antwort).
 *
 * Backend-Endpunkte wie `GET /projects` liefern absichtlich weiter aus, wenn nur
 * die Metadaten-DB klemmt — die Projektliste selbst steht in projects.json. Der
 * Nutzer soll aber wissen, dass er gerade eine unvollstaendige Ansicht sieht.
 */
export function DegradedNotice({ reason, onRetry }: { reason?: string | null; onRetry?: () => void }) {
  if (!reason) {
    return null;
  }
  return (
    <div className="query-degraded" role="status">
      <AlertTriangle size={15} />
      <span>
        Unvollständige Ansicht: Die Metadaten-Datenbank ist gerade nicht lesbar. {reason}
      </span>
      {onRetry ? (
        <button className="button button-compact" type="button" onClick={onRetry}>
          <RefreshCw size={13} />
          <span>Neu laden</span>
        </button>
      ) : null}
    </div>
  );
}

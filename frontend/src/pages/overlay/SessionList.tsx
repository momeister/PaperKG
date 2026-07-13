// Slide-in session list for the AI-Cursor overlay: reopen, rename or delete
// persisted companion/selfdrive sessions (DuckDB `companion_sessions`).

import { useEffect, useState } from "react";
import { Check, Loader2, Pencil, Trash2, X } from "lucide-react";

import { deleteCompanionSession, getCompanionSession, listCompanionSessions, updateCompanionSession } from "../../api";
import type { CompanionSessionDetail, CompanionSessionSummary } from "../../types";

type SessionListProps = {
  activeId: string | null;
  onSelect: (detail: CompanionSessionDetail) => void;
  onClose: () => void;
  onError: (message: string) => void;
};

export function SessionList({ activeId, onSelect, onClose, onError }: SessionListProps) {
  const [sessions, setSessions] = useState<CompanionSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");

  useEffect(() => {
    let alive = true;
    listCompanionSessions()
      .then((res) => {
        if (alive) setSessions(res.sessions ?? []);
      })
      .catch((err) => onError(err instanceof Error ? err.message : String(err)))
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [onError]);

  async function select(id: string) {
    try {
      const detail = await getCompanionSession(id);
      if (detail.error) {
        onError(detail.error);
        return;
      }
      onSelect(detail);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  }

  async function saveTitle(id: string) {
    const title = editTitle.trim();
    setEditingId(null);
    if (!title) return;
    try {
      await updateCompanionSession(id, { title });
      setSessions((prev) => prev.map((item) => (item.id === id ? { ...item, title } : item)));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove(id: string) {
    try {
      await deleteCompanionSession(id);
      setSessions((prev) => prev.filter((item) => item.id !== id));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  }

  const label = (item: CompanionSessionSummary) =>
    (item.title || item.goal || "Ohne Titel").trim() || "Ohne Titel";

  return (
    <div className="overlay-session-list">
      <div className="overlay-session-list__head">
        <strong>Sessions</strong>
        <button type="button" className="overlay-close" aria-label="Schließen" onClick={onClose}>
          <X size={14} />
        </button>
      </div>
      {loading ? (
        <p className="overlay-muted">
          <Loader2 size={13} className="overlay-spin" /> Lade…
        </p>
      ) : sessions.length === 0 ? (
        <p className="overlay-muted">Noch keine Sessions.</p>
      ) : (
        <ul>
          {sessions.map((item) => (
            <li
              key={item.id}
              className={`overlay-session-list__item ${item.id === activeId ? "overlay-session-list__item--active" : ""}`}
            >
              {editingId === item.id ? (
                <span className="overlay-session-list__edit">
                  <input
                    autoFocus
                    value={editTitle}
                    onChange={(event) => setEditTitle(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void saveTitle(item.id);
                      if (event.key === "Escape") setEditingId(null);
                    }}
                  />
                  <button type="button" aria-label="Speichern" onClick={() => void saveTitle(item.id)}>
                    <Check size={13} />
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  className="overlay-session-list__label"
                  onClick={() => void select(item.id)}
                >
                  <span className={`overlay-session-list__kind overlay-session-list__kind--${item.kind}`}>
                    {item.kind === "selfdrive" ? "⚙" : "💬"}
                  </span>
                  <span className="overlay-session-list__title">{label(item)}</span>
                  {item.message_count ? (
                    <span className="overlay-session-list__count">{item.message_count}</span>
                  ) : null}
                </button>
              )}
              <button
                type="button"
                aria-label="Umbenennen"
                onClick={() => {
                  setEditingId(item.id);
                  setEditTitle(label(item));
                }}
              >
                <Pencil size={12} />
              </button>
              <button type="button" aria-label="Löschen" onClick={() => void remove(item.id)}>
                <Trash2 size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

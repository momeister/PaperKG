// Klarstellungs-Dialog der Tiefenanalyse (Schwerpunkt-Auswahl vor dem Start) —
// aus WorkspacePage.tsx extrahiert. Reiner Praesentations-Dialog; State/Handler
// bleiben in der Page und kommen als Props.

export function ClarifyDialog({
  directions,
  selected,
  freetext,
  onToggleDirection,
  onFreetextChange,
  onFinish,
}: {
  directions: string[];
  selected: number[];
  freetext: string;
  onToggleDirection: (index: number) => void;
  onFreetextChange: (value: string) => void;
  onFinish: (skip: boolean) => void;
}) {
  return (
    <div className="harvest-dialog-overlay">
      <div
        className="harvest-dialog-card clarify-dialog-card"
        tabIndex={-1}
        ref={(el) => {
          if (el && !el.contains(document.activeElement)) el.focus();
        }}
        onKeyDown={(e) => {
          const inInput = (e.target as HTMLElement).tagName === "INPUT";
          if (e.key === "Enter") {
            e.preventDefault();
            onFinish(false);
          } else if (e.key === "Escape") {
            e.preventDefault();
            onFinish(true);
          } else if (!inInput && /^[1-9]$/.test(e.key)) {
            const idx = Number(e.key) - 1;
            if (idx < directions.length) {
              e.preventDefault();
              onToggleDirection(idx);
            }
          }
        }}
      >
        <strong>In welche Richtung soll die Analyse gehen?</strong>
        <p>
          Wähle Schwerpunkte mit den Zahlentasten <kbd>1</kbd>–<kbd>9</kbd> oder per Klick,
          ergänze optional eine eigene Richtung und starte mit <kbd>Enter</kbd>.
        </p>
        <div className="clarify-directions">
          {directions.map((dir, i) => {
            const active = selected.includes(i);
            return (
              <button
                key={i}
                type="button"
                className={`clarify-direction${active ? " clarify-direction--active" : ""}`}
                onClick={() => onToggleDirection(i)}
              >
                <span className="clarify-direction-num">{i + 1}</span>
                <span className="clarify-direction-label">{dir}</span>
                {active ? <span className="clarify-direction-check">✓</span> : null}
              </button>
            );
          })}
        </div>
        <div className="clarify-question-block">
          <label className="clarify-question-label">Eigene Richtung / Anmerkungen</label>
          <input
            type="text"
            className="clarify-question-input"
            placeholder="z.B. Fokus auf klinische Anwendungen, ab 2020, ..."
            value={freetext}
            onChange={(e) => onFreetextChange(e.target.value)}
          />
        </div>
        <div className="harvest-dialog-actions">
          <button
            type="button"
            className="button button-primary button-compact"
            onClick={() => onFinish(false)}
          >
            Analyse starten
          </button>
          <button
            type="button"
            className="button button-compact"
            onClick={() => onFinish(true)}
          >
            Überspringen
          </button>
        </div>
      </div>
    </div>
  );
}

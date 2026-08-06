import type { CreativityLevel } from "../types";

/**
 * 5-stufiger Kreativitäts-Slider für Task-Focused Mode + Parallel-Research.
 *
 * Bedeutung (Plan §Session 4):
 *  1 = konservativ / Mainstream
 *  3 = ausgewogen (Default)
 *  5 = aggressiv / cross-domain
 *
 * Wiederverwendbar — weder Project-Kenntnis noch API-Calls hierdrin.
 * Persistenz passiert beim Aufrufer (state.tsx → project_meta.json).
 */
const LEVELS: { value: CreativityLevel; label: string; hint: string }[] = [
  { value: 1, label: "1", hint: "Konservativ" },
  { value: 2, label: "2", hint: "Vorsichtig" },
  { value: 3, label: "3", hint: "Ausgewogen" },
  { value: 4, label: "4", hint: "Mutig" },
  { value: 5, label: "5", hint: "Cross-domain" },
];

type Props = {
  value: CreativityLevel;
  onChange: (level: CreativityLevel) => void;
  /** Kompakte Darstellung (nur Schiene + Zahlen, kein Panel-Rahmen). */
  compact?: boolean;
  /** Deaktiviert die Interaktion (z. B. während LLM läuft). */
  disabled?: boolean;
  /** Optionaler Titel über der Schiene — Default: "Kreativität". */
  label?: string;
  id?: string;
  /** Klärungstext unter der Schiene (auch im compact-Modus sichtbar). Erklärt
   *  Geltungsbereich der Kreativitätseinstellung (z. B. projektweit vs. nur
   *  für diese Vorschlag-Generierung). */
  hint?: string;
  /** Native Browser-Tooltipp (title-Attribut auf dem Container). Für
   *  ausführlichere Erklärungen beim Hovern. */
  tooltip?: string;
};

export function CreativitySlider({
  value,
  onChange,
  compact = false,
  disabled = false,
  label = "Kreativität",
  id = "creativity-slider",
  hint,
  tooltip,
}: Props) {
  const active = LEVELS.find((level) => level.value === value) ?? LEVELS[2];

  return (
    <div
      className={compact ? "creativity-slider creativity-slider--compact" : "creativity-slider"}
      id={id}
      title={tooltip}
    >
      {!compact ? (
        <div className="creativity-slider-head">
          <label htmlFor={id} className="creativity-slider-label">
            {label}
          </label>
          <span className="creativity-slider-value">
            <strong>{active.value}</strong>
            <span className="muted"> · {active.hint}</span>
          </span>
        </div>
      ) : null}
      <div className="creativity-slider-track" role="radiogroup" aria-label={label}>
        {LEVELS.map((level) => {
          const isActive = level.value === value;
          return (
            <button
              key={level.value}
              type="button"
              role="radio"
              aria-checked={isActive}
              aria-label={`${label} ${level.value} — ${level.hint}`}
              className={isActive ? "creativity-slider-step active" : "creativity-slider-step"}
              onClick={() => !disabled && onChange(level.value)}
              disabled={disabled}
            >
              <span className="creativity-slider-step-num">{level.label}</span>
              {!compact ? <span className="creativity-slider-step-hint">{level.hint}</span> : null}
            </button>
          );
        })}
      </div>
      {hint ? <p className="creativity-slider-hint muted">{hint}</p> : null}
    </div>
  );
}
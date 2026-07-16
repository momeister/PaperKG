/** Marken-Zeichen: eine kleine Paper-Konstellation — vier verbundene Knoten,
 *  das Echo des Pipeline-Graphen im Forschung-Hub. Erbt currentColor; der
 *  Akzentknoten nutzt var(--accent). */
export function ConstellationMark({ size = 22 }: { size?: number }) {
  return (
    <svg
      className="constellation-mark"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      role="img"
      aria-hidden="true"
    >
      <path d="M5 17.5 10.5 7l6 4.5L19.5 5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" opacity="0.55" />
      <circle cx="5" cy="17.5" r="2" fill="currentColor" opacity="0.8" />
      <circle cx="10.5" cy="7" r="2.4" fill="var(--accent, currentColor)" />
      <circle cx="16.5" cy="11.5" r="1.8" fill="currentColor" opacity="0.65" />
      <circle cx="19.5" cy="5" r="1.5" fill="currentColor" opacity="0.5" />
    </svg>
  );
}

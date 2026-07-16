import type { ReactNode } from "react";
import { LazyMotion, MotionConfig, m } from "framer-motion";

const loadFeatures = () => import("./motionFeatures").then((mod) => mod.default);

/** Umhüllt nur die Haupt-Shell (nicht die Overlay-Fenster): lädt die
 *  Animations-Features lazy und respektiert die OS-Einstellung
 *  "Bewegung reduzieren" (reducedMotion="user"). */
export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={loadFeatures} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}

export { m };

export const EASE_OUT = [0.22, 1, 0.36, 1] as const;

/** Standard-Eintritt: sanft von unten einblenden. */
export const fadeUp = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.2, ease: EASE_OUT }
};

/** Container-Variante: Kinder gestaffelt eintreten lassen. */
export const listStagger = {
  initial: "hidden",
  animate: "show",
  variants: { show: { transition: { staggerChildren: 0.025 } } }
};

export const listItem = {
  variants: {
    hidden: { opacity: 0, y: 6 },
    show: { opacity: 1, y: 0, transition: { duration: 0.18, ease: EASE_OUT } }
  }
};

/** Seiten-/Stufen-Eintritt (nur enter, kein Exit — verträgt sich mit Suspense
 *  und hält die Playwright-Locator stabil). */
export function PageEnter({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <m.div className={className} {...fadeUp}>
      {children}
    </m.div>
  );
}

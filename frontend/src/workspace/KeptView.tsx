import { PaneVisibilityContext } from "./PortablePane";
import { useContext, useRef, type ReactNode } from "react";

/** Mount on first visit, then retain the controller while another tab is shown. */
export function KeptView({ active, children }: { active: boolean; children: ReactNode }) {
  const parentVisible = useContext(PaneVisibilityContext);
  const visited = useRef(active);
  if (active) visited.current = true;
  return visited.current ? <div className="workspace-kept-view" hidden={!active}><PaneVisibilityContext.Provider value={active && parentVisible}>{children}</PaneVisibilityContext.Provider></div> : null;
}

import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { AppStateContext, useAppState, type AppState } from "../state";
import { WorkspaceOwnerContext, WorkspaceWindowsContext } from "./PortablePane";
import { RESTORE_LAYOUT_KEY, WorkspaceWindows } from "./windows";
import { ShutdownGuard } from "./ShutdownGuard";

const WorkspaceController = lazy(() => import("../pages/WorkspacePage").then(m => ({ default: m.WorkspacePage })));

/** One persistent controller per visited project. Route changes only hide its
 * dock slots. Requests retain the project/session/provider captured on submit. */
export function WorkspaceHost() {
  const state = useAppState();
  const location = useLocation();
  const visible = location.pathname === "/workspace";
  const owner = state.activeProject ?? "";
  const [manager] = useState(() => new WorkspaceWindows());
  const contexts = useRef(new Map<string, AppState>());
  if (visible || contexts.current.has(owner) || manager.hasActiveWindows() || localStorage.getItem(RESTORE_LAYOUT_KEY) === "true") {
    contexts.current.set(owner, state);
  }
  useEffect(() => manager.start(), [manager]);
  useLayoutEffect(() => manager.setOwner(owner), [manager, owner]);
  return <WorkspaceWindowsContext.Provider value={manager}>
    <ShutdownGuard />
    {[...contexts.current].map(([project, context]) => <AppStateContext.Provider key={project} value={context}>
      <WorkspaceOwnerContext.Provider value={project}>
        <div className="workspace-controller" hidden={!visible || project !== owner}>
          <Suspense fallback={<div className="page-loading">Arbeitsplatz wird geladen…</div>}>
            <WorkspaceController />
            {project === owner ? <RestoreWindows manager={manager} /> : null}
          </Suspense>
        </div>
      </WorkspaceOwnerContext.Provider>
    </AppStateContext.Provider>)}
  </WorkspaceWindowsContext.Provider>;
}
const restored = new WeakSet<WorkspaceWindows>();
function RestoreWindows({ manager }: { manager: WorkspaceWindows }) {
  useEffect(() => {
    if (restored.has(manager)) return;
    restored.add(manager); void manager.restore();
  }, [manager]);
  return null;
}

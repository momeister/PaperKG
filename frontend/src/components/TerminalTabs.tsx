import { useRef, useState } from "react";
import { Plus, TerminalSquare, X } from "lucide-react";

import { WerkstattTerminal } from "./WerkstattTerminal";
import { isTauri } from "../native";

// Mehrere parallele Terminals in einer Tab-Leiste. Das Rust-Backend
// (portable-pty) verwaltet bereits beliebig viele Sessions; hier rendern wir
// alle Terminals gleichzeitig (inaktive via display:none), damit PTY-Session und
// Scrollback beim Tab-Wechsel erhalten bleiben. `onOutput` reicht die Ausgabe
// hoch (für die Vorschau-URL-Auto-Erkennung).

type TermTab = { key: string; title: string };

export function TerminalTabs({ cwd, onOutput }: { cwd: string; onOutput?: (text: string) => void }) {
  const counter = useRef(1);
  const [tabs, setTabs] = useState<TermTab[]>(() => [{ key: "t1", title: "Terminal 1" }]);
  const [active, setActive] = useState("t1");

  function addTab() {
    counter.current += 1;
    const key = `t${counter.current}`;
    setTabs((current) => [...current, { key, title: `Terminal ${counter.current}` }]);
    setActive(key);
  }

  function closeTab(key: string) {
    setTabs((current) => {
      const next = current.filter((tab) => tab.key !== key);
      if (next.length === 0) return current; // immer mindestens ein Terminal behalten
      if (key === active) setActive(next[next.length - 1].key);
      return next;
    });
  }

  if (!isTauri()) {
    return (
      <div className="werkstatt-terminal-hint">
        Das eingebettete Terminal (für Claude Code, opencode, codex, git …) ist nur in der Desktop-App
        verfügbar.
      </div>
    );
  }

  return (
    <div className="wk-term-tabs">
      <div className="wk-term-tabbar">
        {tabs.map((tab) => (
          <div
            key={tab.key}
            className={`wk-term-tab ${tab.key === active ? "active" : ""}`}
            onClick={() => setActive(tab.key)}
          >
            <TerminalSquare size={13} />
            <span>{tab.title}</span>
            {tabs.length > 1 ? (
              <button
                className="wk-term-tab-close"
                type="button"
                title="Terminal schließen"
                onClick={(event) => {
                  event.stopPropagation();
                  closeTab(tab.key);
                }}
              >
                <X size={12} />
              </button>
            ) : null}
          </div>
        ))}
        <button className="wk-term-tab-add" type="button" title="Neues Terminal" onClick={addTab}>
          <Plus size={14} />
        </button>
      </div>
      <div className="wk-term-bodies">
        {tabs.map((tab) => (
          <div
            key={tab.key}
            className="wk-term-body"
            style={{ display: tab.key === active ? "flex" : "none" }}
          >
            <WerkstattTerminal cwd={cwd} active={tab.key === active} onOutput={onOutput} />
          </div>
        ))}
      </div>
    </div>
  );
}

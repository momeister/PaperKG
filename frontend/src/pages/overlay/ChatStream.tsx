// The unified overlay chat stream: user/assistant bubbles with sources, plus the
// small system lines (executed actions, verification verdicts, research notes).

import type { RefObject } from "react";

import type { OverlayChatEntry } from "./companionSession";

function Bubble({ entry }: { entry: OverlayChatEntry }) {
  if (entry.role === "system") {
    return (
      <div
        className={`overlay-chat-system ${entry.verification && entry.verification.ok === false ? "overlay-chat-system--warn" : ""}`}
      >
        {entry.text}
      </div>
    );
  }
  return (
    <div className={`overlay-chat-bubble overlay-chat-bubble--${entry.role}`}>
      {entry.text}
      {entry.sources?.length ? (
        <ul className="overlay-chat-sources">
          {entry.sources.map((source, index) => (
            <li key={index}>
              {source.type === "paper" ? (
                <span>📄 [{source.id}] {source.title}</span>
              ) : (
                <a href={source.url} target="_blank" rel="noreferrer">
                  🌐 {source.title || source.url}
                </a>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function ChatStream({
  entries,
  endRef,
  emptyHint,
}: {
  entries: OverlayChatEntry[];
  endRef: RefObject<HTMLDivElement>;
  emptyHint: string;
}) {
  if (!entries.length) return <p className="overlay-muted">{emptyHint}</p>;
  return (
    <div className="overlay-chat">
      {entries.map((entry, index) => (
        <Bubble key={index} entry={entry} />
      ))}
      <div ref={endRef} />
    </div>
  );
}

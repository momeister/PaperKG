import { useEffect, type RefObject } from "react";

export function textareaCaretTop(node: HTMLTextAreaElement): { top: number; height: number } {
  const style = getComputedStyle(node);
  const document = node.ownerDocument;
  const mirror = document.createElement("div");
  for (const prop of ["fontFamily", "fontSize", "fontWeight", "fontStyle", "lineHeight", "letterSpacing", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "textIndent", "tabSize", "wordSpacing", "wordBreak", "overflowWrap"] as const) mirror.style[prop] = style[prop];
  Object.assign(mirror.style, { position: "fixed", left: "-10000px", top: "0", visibility: "hidden", width: `${node.clientWidth}px`, boxSizing: "border-box", whiteSpace: "pre-wrap", overflowWrap: "break-word" });
  mirror.append(document.createTextNode(node.value.slice(0, node.selectionStart)));
  const marker = document.createElement("span");
  marker.textContent = node.value.slice(node.selectionStart, node.selectionStart + 1) || "\u200b";
  mirror.append(marker, document.createTextNode(node.value.slice(node.selectionStart + 1)));
  document.body.append(mirror);
  const uiScale = node.getBoundingClientRect().width / Math.max(1, node.offsetWidth);
  const result = { top: (marker.getBoundingClientRect().top - mirror.getBoundingClientRect().top) / uiScale, height: parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.4 };
  mirror.remove();
  return result;
}

/** Preserve the user's current writing line; explicit navigation establishes a new anchor. */
export function useWritingAnchor(ref: RefObject<HTMLTextAreaElement>, enabled: boolean, view: string) {
  useEffect(() => {
    const node = ref.current;
    if (!node || !enabled) return;
    let anchor: number | null = null, frame = 0, typing = false, programmaticTop: number | null = null;
    const reset = () => { anchor = null; };
    const beforeInput = () => {
      const caret = textareaCaretTop(node);
      const visible = caret.top - node.scrollTop;
      if (visible < 0 || visible + caret.height > node.clientHeight) {
        node.scrollTop = Math.max(0, caret.top - Math.max(0, Math.min(node.clientHeight - caret.height * 2, visible)));
        anchor = caret.top - node.scrollTop;
      } else if (anchor === null) anchor = visible;
      typing = true;
    };
    const input = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (anchor !== null) {
          const caret = textareaCaretTop(node);
          node.scrollTop = Math.max(0, caret.top - anchor);
          programmaticTop = node.scrollTop;
        }
        typing = false;
      });
    };
    const scroll = () => { if (!typing && node.scrollTop !== programmaticTop) reset(); programmaticTop = null; };
    const key = (event: KeyboardEvent) => { if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(event.key)) reset(); };
    node.addEventListener("beforeinput", beforeInput);
    node.addEventListener("input", input);
    node.addEventListener("wheel", reset, { passive: true });
    node.addEventListener("pointerdown", reset);
    node.addEventListener("keydown", key);
    node.addEventListener("scroll", scroll);
    const observer = new ResizeObserver(reset);
    observer.observe(node);
    return () => {
      cancelAnimationFrame(frame); observer.disconnect();
      node.removeEventListener("beforeinput", beforeInput); node.removeEventListener("input", input);
      node.removeEventListener("wheel", reset); node.removeEventListener("pointerdown", reset);
      node.removeEventListener("keydown", key); node.removeEventListener("scroll", scroll);
    };
  }, [ref, enabled, view]);
}

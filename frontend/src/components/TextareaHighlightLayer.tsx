export type TextHighlightRange = {
  start: number;
  end: number;
  className?: string;
};

export type TextHighlightInsertion = {
  index: number;
  content: string;
  className?: string;
};

type TextareaHighlightLayerProps = {
  text: string;
  ranges?: TextHighlightRange[];
  insertions?: TextHighlightInsertion[];
  scrollTop?: number;
};

export function TextareaHighlightLayer({ text, ranges = [], insertions = [], scrollTop = 0 }: TextareaHighlightLayerProps) {
  const normalizedRanges = ranges
    .map((range) => ({
      ...range,
      start: Math.max(0, Math.min(text.length, range.start)),
      end: Math.max(0, Math.min(text.length, range.end))
    }))
    .filter((range) => range.end > range.start);
  const normalizedInsertions = insertions
    .map((insertion) => ({ ...insertion, index: Math.max(0, Math.min(text.length, insertion.index)) }))
    .filter((insertion) => insertion.content);
  const points = Array.from(
    new Set([0, text.length, ...normalizedRanges.flatMap((range) => [range.start, range.end]), ...normalizedInsertions.map((insertion) => insertion.index)])
  ).sort((left, right) => left - right);

  return (
    <div className="textarea-highlight-layer" aria-hidden="true">
      <div style={{ transform: `translateY(-${scrollTop}px)` }}>
        {points.map((point, index) => {
          const nextPoint = points[index + 1];
          const insertionsAtPoint = normalizedInsertions.filter((insertion) => insertion.index === point);
          const range = normalizedRanges.find((item) => point >= item.start && point < item.end);
          return (
            <span key={`${point}-${index}`}>
              {insertionsAtPoint.map((insertion, insertionIndex) => (
                <span className={`textarea-ghost-insertion ${insertion.className ?? ""}`} key={`${point}-insert-${insertionIndex}`}>
                  {insertion.content}
                </span>
              ))}
              {nextPoint !== undefined ? renderSegment(text.slice(point, nextPoint), range?.className) : null}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function renderSegment(value: string, className?: string) {
  if (!value) {
    return null;
  }
  return className ? <mark className={className}>{value}</mark> : <span>{value}</span>;
}

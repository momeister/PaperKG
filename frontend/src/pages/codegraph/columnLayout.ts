/**
 * Spaltenlayout: wer aufruft nach links, wer aufgerufen wird nach rechts.
 *
 * Das kräftebasierte Layout der Paper-Seite ist hier falsch. Es ordnet nach
 * Verbundenheit, nicht nach Richtung — und bei einem Aufrufgraphen *ist* die
 * Richtung die Aussage: links steht, was hereinkommt, rechts, was
 * herausgeht, in der Mitte das betrachtete Symbol. Ein Knäuel, in dem man die
 * Pfeilspitzen einzeln absuchen muss, beantwortet die Frage nicht, für die man
 * die Karte aufgemacht hat.
 *
 * Kein Kräftespiel, keine Zufallszahlen: die Position eines Knotens hängt nur
 * von seiner Spalte und seinem Rang darin ab. Damit springt beim Ausklappen
 * auch nichts — was schon steht, steht danach wieder da, sofern es seine Spalte
 * behält.
 */
export type ColumnNode = {
  id: string;
  /** Vorzeichenbehaftet: negativ = Aufrufer (links), 0 = Fokus, positiv = Aufgerufene. */
  column: number;
  /** Sortierschlüssel innerhalb der Spalte; höher steht weiter oben. */
  weight: number;
  height?: number;
};

export type ColumnPoint = { x: number; y: number };

export type ColumnLayoutOptions = {
  /** Waagerechter Abstand der Spaltenmitten. */
  columnGap?: number;
  /** Senkrechter Abstand zweier Knoten derselben Spalte. */
  rowGap?: number;
  /** Ab wie vielen Knoten eine Spalte in Unterspalten zerfällt. */
  maxPerColumn?: number;
};

const DEFAULT_COLUMN_GAP = 280;
const DEFAULT_ROW_GAP = 74;
/**
 * Zweiundzwanzig Aufrufer in einer Reihe ergeben einen Streifen, der höher ist
 * als jedes Fenster — und zwingen die Ansicht so weit heraus, dass kein Name
 * mehr lesbar ist. Ab dieser Zahl bricht eine Spalte in Unterspalten um, die
 * *vom Fokus weg* wandern: die Seite bleibt die Aussage, die Höhe wird zahm.
 */
const DEFAULT_MAX_PER_COLUMN = 12;
const LANE_GAP = 190;

/**
 * Positionen berechnen.
 *
 * Die Spalten werden getrennt zentriert: eine Spalte mit drei Knoten steht
 * mittig neben einer mit dreissig, statt oben zu kleben. Innerhalb einer Spalte
 * entscheidet `weight` (Relevanz) über die Reihenfolge — das Wichtigste liegt
 * auf Augenhöhe des Fokus, nicht am unteren Rand.
 */
export function computeColumnLayout(
  nodes: ColumnNode[],
  options: ColumnLayoutOptions = {},
): Map<string, ColumnPoint> {
  const columnGap = options.columnGap ?? DEFAULT_COLUMN_GAP;
  const rowGap = options.rowGap ?? DEFAULT_ROW_GAP;
  const maxPerColumn = Math.max(1, options.maxPerColumn ?? DEFAULT_MAX_PER_COLUMN);
  const result = new Map<string, ColumnPoint>();
  if (!nodes.length) return result;

  const byColumn = new Map<number, ColumnNode[]>();
  for (const node of nodes) {
    const list = byColumn.get(node.column);
    if (list) list.push(node);
    else byColumn.set(node.column, [node]);
  }

  for (const [column, members] of byColumn) {
    // Stabil: bei gleicher Relevanz entscheidet die ID, nicht die Ladereihenfolge.
    members.sort((a, b) => b.weight - a.weight || a.id.localeCompare(b.id));

    // Unterspalten: die wichtigsten bleiben beim Fokus, der Rest wandert nach
    // aussen. Die Richtung (links/rechts) bleibt dabei unangetastet.
    const laneCount = Math.max(1, Math.ceil(members.length / maxPerColumn));
    const perLane = Math.ceil(members.length / laneCount);
    // Spalte 0 (der Fokus) wandert nirgendwohin; sonst weg von der Mitte.
    const laneDirection = column === 0 ? 0 : Math.sign(column);

    for (let lane = 0; lane < laneCount; lane++) {
      const slice = members.slice(lane * perLane, (lane + 1) * perLane);
      if (!slice.length) continue;

      // Die wichtigsten in die Mitte, abwechselnd nach oben und unten. Sonst
      // stünde das Wichtigste ganz oben und das Auge müsste erst wandern.
      const ordered: ColumnNode[] = [];
      slice.forEach((node, index) => {
        if (index % 2 === 0) ordered.push(node);
        else ordered.unshift(node);
      });

      const span = (ordered.length - 1) * rowGap;
      const x = column * columnGap + laneDirection * lane * LANE_GAP;
      ordered.forEach((node, index) => {
        result.set(node.id, { x, y: index * rowGap - span / 2 });
      });
    }
  }

  return result;
}

/** Beschriftung einer Spalte. Die Karte sagt selbst, was links und rechts heisst. */
export function columnLabel(column: number): string {
  if (column === 0) return "betrachtet";
  if (column === -1) return "ruft dieses Symbol auf";
  if (column === 1) return "wird von hier aufgerufen";
  return column < 0 ? `${Math.abs(column)} Schritte davor` : `${column} Schritte danach`;
}

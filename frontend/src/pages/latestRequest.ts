/** A request owns its starting settings; only the latest generation may publish. */
export class LatestRequest {
  generation = 0;
  start<T extends object>(settings: T) {
    const id = ++this.generation;
    return { id, settings: Object.freeze(structuredClone(settings)), isCurrent: () => id === this.generation };
  }
  invalidate() { this.generation++; }
}

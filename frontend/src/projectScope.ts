export const ALL_PAPERS_NOTES_PROJECT_ID = "__all_papers__";

export function noteProjectId(activeProject?: string) {
  return activeProject || ALL_PAPERS_NOTES_PROJECT_ID;
}

export function projectScopeLabel(activeProject?: string) {
  return activeProject || "Alle Papers";
}

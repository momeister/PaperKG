import { createContext, useContext } from "react";

export type LlmParams = {
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  context_size?: number;
};

export type AppState = {
  activeProject?: string;
  setActiveProject: (project?: string) => void;
  provider?: string;
  setProvider: (provider?: string) => void;
  model?: string;
  setModel: (model?: string) => void;
  llmParams: LlmParams;
  setLlmParams: (params: LlmParams) => void;
};

export const AppStateContext = createContext<AppState | null>(null);

export function useAppState() {
  const state = useContext(AppStateContext);
  if (!state) {
    throw new Error("AppStateContext is missing.");
  }
  return state;
}

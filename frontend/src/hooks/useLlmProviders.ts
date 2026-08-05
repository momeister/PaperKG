/**
 * Anbieter- und Modellliste — einmal beschrieben, überall gleich.
 *
 * Die Reihenfolge der Modelloptionen ist keine Kosmetik: lokal *erkannte*
 * Modelle stehen vorn, weil LM Studio und Ollama am besten wissen, was gerade
 * geladen ist; danach kommt, was in `config.yaml` steht; zuletzt das aktuell
 * gewählte, damit eine von Hand eingetragene Wahl nicht aus der Liste fällt und
 * damit unsichtbar würde.
 *
 * Die `queryKey`s sind absichtlich dieselben wie bisher in `App.tsx` — so teilen
 * sich Topbar, Settings, Benchmarks, Overlay und der Code-Graph einen Cache,
 * statt viermal dieselbe Erkennung anzustossen.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import type { Provider } from "../types";

/** Anbieter, die ihre geladenen Modelle über eine API melden können. */
const DISCOVERY_TYPES = ["lm_studio", "ollama", "openai_compatible", "openai", "nvidia"];

export type LlmProvidersResult = {
  providers: Provider[];
  defaultProvider?: string;
  selectedProvider?: Provider;
  modelOptions: string[];
  supportsDiscovery: boolean;
  isDiscovering: boolean;
  isError: boolean;
};

export function useLlmProviders(
  provider?: string,
  model?: string,
  options: { enabled?: boolean } = {},
): LlmProvidersResult {
  const enabled = options.enabled ?? true;

  const providersQuery = useQuery({
    queryKey: ["providers"],
    queryFn: api.getProviders,
    enabled,
  });

  const selectedProvider = providersQuery.data?.providers.find((item) => item.name === provider);
  const supportsDiscovery = DISCOVERY_TYPES.includes(selectedProvider?.provider_type ?? "");

  const discoveredQuery = useQuery({
    queryKey: ["models-discovered", provider],
    queryFn: () => api.discoverModels(provider as string),
    enabled: enabled && Boolean(provider) && supportsDiscovery,
    staleTime: 60_000,
    retry: false,
  });

  const modelOptions = useMemo(() => {
    const merged = [...(discoveredQuery.data?.models ?? []), ...(selectedProvider?.models ?? [])];
    if (selectedProvider?.default_model) merged.push(selectedProvider.default_model);
    if (model) merged.push(model);
    return Array.from(new Set(merged.filter(Boolean)));
  }, [discoveredQuery.data?.models, selectedProvider, model]);

  return {
    providers: providersQuery.data?.providers ?? [],
    defaultProvider: providersQuery.data?.default_provider,
    selectedProvider,
    modelOptions,
    supportsDiscovery,
    isDiscovering: discoveredQuery.isFetching,
    isError: providersQuery.isError,
  };
}

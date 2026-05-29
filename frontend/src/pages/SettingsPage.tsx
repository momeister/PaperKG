import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, RefreshCcw } from "lucide-react";

import { api, API_BASE_URL } from "../api";
import { Status } from "../components/Status";
import { useAppState } from "../state";

export function SettingsPage() {
  const { provider, setProvider, model, setModel } = useAppState();
  const queryClient = useQueryClient();
  const providersQuery = useQuery({ queryKey: ["providers"], queryFn: api.getProviders });
  const discover = useMutation({
    mutationFn: api.discoverModels,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] })
  });
  const check = useMutation({ mutationFn: ({ providerName, modelName }: { providerName: string; modelName?: string }) => api.checkProvider(providerName, modelName) });

  return (
    <section className="page">
      <div className="page-title">
        <div>
          <span>Runtime</span>
          <h1>Settings</h1>
        </div>
      </div>

      <section className="panel">
        <div className="settings-grid">
          <label>
            API Base URL
            <input value={API_BASE_URL} readOnly />
          </label>
          <label>
            Provider
            <select value={provider ?? ""} onChange={(event) => setProvider(event.target.value || undefined)}>
              {(providersQuery.data?.providers ?? []).map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Modell
            <input value={model ?? ""} onChange={(event) => setModel(event.target.value || undefined)} placeholder="Default" />
          </label>
        </div>
      </section>

      <div className="provider-grid">
        {(providersQuery.data?.providers ?? []).map((item) => (
          <section className="provider-card" key={item.name}>
            <div className="panel-heading">
              <div>
                <span>{item.provider_type}</span>
                <strong>{item.name}</strong>
              </div>
              <Status value={item.auth_configured ? "true" : "local"} />
            </div>
            <dl>
              <dt>Base URL</dt>
              <dd>{item.base_url}</dd>
              <dt>Default Model</dt>
              <dd>{item.default_model}</dd>
              <dt>Context</dt>
              <dd>{item.settings.context_size ?? "n/a"}</dd>
            </dl>
            <div className="button-row">
              <button className="button" onClick={() => discover.mutate(item.name)} disabled={discover.isPending}>
                <RefreshCcw size={16} />
                <span>Discover</span>
              </button>
              <button className="button button-primary" onClick={() => check.mutate({ providerName: item.name, modelName: model })} disabled={check.isPending}>
                <CheckCircle2 size={16} />
                <span>Check</span>
              </button>
            </div>
            <div className="model-list">
              {item.models.map((modelName) => (
                <button key={modelName} className={model === modelName ? "active" : ""} onClick={() => setModel(modelName)}>
                  {modelName}
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>

      {check.data ? (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span>Provider Check</span>
              <strong>
                {check.data.provider} · {check.data.model}
              </strong>
            </div>
            <Status value={check.data.ok ? "success" : "failed"} />
          </div>
          {check.data.error ? <div className="warning-row">{check.data.error}</div> : null}
        </section>
      ) : null}
    </section>
  );
}

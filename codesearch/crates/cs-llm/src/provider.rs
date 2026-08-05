//! Talking to a model, local first.
//!
//! Everything speaks the OpenAI chat-completions shape, which is the one thing
//! Ollama, LM Studio, llama.cpp's server and vLLM all agree on. That means one
//! client rather than four, and it means a model can be swapped without touching
//! anything above this file.
//!
//! Discovery probes **localhost only**. The app makes no outbound connection
//! unless a cloud provider is configured by hand, and the UI shows where a
//! request is going before it goes there.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::time::Duration;

/// Ports the usual local runners listen on. Probing is cheap and the payoff is
/// that a working setup needs no configuration at all.
const LOCAL_CANDIDATES: &[(&str, &str)] = &[
    ("Ollama", "http://127.0.0.1:11434/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1"),
    ("llama.cpp", "http://127.0.0.1:8080/v1"),
    ("vLLM", "http://127.0.0.1:8000/v1"),
];

/// Discovery must not make the app feel slow to start, and a local server either
/// answers immediately or is not there.
const PROBE_TIMEOUT: Duration = Duration::from_millis(600);

/// Generous, because the first call to a local server also pays for loading the
/// model into memory — on a modest machine with a 9B model that alone can take
/// minutes. The UI can cancel at any point; this is only the ceiling.
///
/// Override with `CS_LLM_TIMEOUT` (seconds) when driving a slow remote endpoint.
fn chat_timeout() -> Duration {
    std::env::var("CS_LLM_TIMEOUT")
        .ok()
        .and_then(|value| value.parse().ok())
        .map(Duration::from_secs)
        .unwrap_or(Duration::from_secs(600))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Provider {
    /// Shown in the model picker: "Ollama", "LM Studio", "Anthropic".
    pub name: String,
    /// Base URL including `/v1`.
    pub base_url: String,
    pub api_key: Option<String>,
    pub models: Vec<String>,
    /// False for anything not on the loopback interface. Drives the warning the
    /// UI shows before code leaves the machine.
    pub local: bool,
}

impl Provider {
    fn is_local(base_url: &str) -> bool {
        base_url.contains("127.0.0.1") || base_url.contains("localhost") || base_url.contains("[::1]")
    }
}

/// Probes the well-known local ports and returns whatever answered.
///
/// Failures are silent by design: "LM Studio is not running" is the normal case,
/// not an error worth surfacing.
pub fn discover_local() -> Vec<Provider> {
    let agent = ureq::Agent::config_builder()
        .timeout_global(Some(PROBE_TIMEOUT))
        .build()
        .new_agent();

    LOCAL_CANDIDATES
        .iter()
        .filter_map(|(name, base_url)| {
            let models = list_models(&agent, base_url, None).ok()?;
            if models.is_empty() {
                return None;
            }
            tracing::info!(provider = name, count = models.len(), "local model server found");
            Some(Provider {
                name: (*name).to_string(),
                base_url: (*base_url).to_string(),
                api_key: None,
                models,
                local: true,
            })
        })
        .collect()
}

#[derive(Deserialize)]
struct ModelList {
    data: Vec<ModelEntry>,
}

#[derive(Deserialize)]
struct ModelEntry {
    id: String,
}

fn list_models(agent: &ureq::Agent, base_url: &str, api_key: Option<&str>) -> Result<Vec<String>> {
    let mut request = agent.get(&format!("{base_url}/models"));
    if let Some(key) = api_key {
        request = request.header("Authorization", &format!("Bearer {key}"));
    }
    let mut response = request.call()?;
    let list: ModelList = response.body_mut().read_json()?;
    let mut models: Vec<String> = list.data.into_iter().map(|entry| entry.id).collect();
    models.sort();
    Ok(models)
}

/// Checks a manually configured provider and fills in its model list.
pub fn probe(name: &str, base_url: &str, api_key: Option<&str>) -> Result<Provider> {
    let agent = ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(10)))
        .build()
        .new_agent();

    let models = list_models(&agent, base_url, api_key)
        .with_context(|| format!("{name} unter {base_url} antwortet nicht"))?;

    Ok(Provider {
        name: name.to_string(),
        base_url: base_url.to_string(),
        api_key: api_key.map(str::to_string),
        models,
        local: Provider::is_local(base_url),
    })
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    System,
    User,
    Assistant,
    Tool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: Role,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<ToolCall>>,
    /// Set on tool results, matching the call being answered.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
}

impl Message {
    pub fn system(content: impl Into<String>) -> Self {
        Self { role: Role::System, content: Some(content.into()), tool_calls: None, tool_call_id: None }
    }

    pub fn user(content: impl Into<String>) -> Self {
        Self { role: Role::User, content: Some(content.into()), tool_calls: None, tool_call_id: None }
    }

    pub fn tool_result(call_id: impl Into<String>, content: impl Into<String>) -> Self {
        Self {
            role: Role::Tool,
            content: Some(content.into()),
            tool_calls: None,
            tool_call_id: Some(call_id.into()),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    #[serde(rename = "type", default = "default_tool_type")]
    pub call_type: String,
    pub function: FunctionCall,
}

fn default_tool_type() -> String {
    "function".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FunctionCall {
    pub name: String,
    /// A JSON object, as a string. Models emit this inconsistently, so parsing is
    /// the caller's problem and is done defensively.
    pub arguments: String,
}

#[derive(Debug, Serialize)]
pub struct ToolSpec {
    #[serde(rename = "type")]
    pub spec_type: &'static str,
    pub function: FunctionSpec,
}

#[derive(Debug, Serialize)]
pub struct FunctionSpec {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,
}

#[derive(Serialize)]
struct ChatRequest<'a> {
    model: &'a str,
    messages: &'a [Message],
    #[serde(skip_serializing_if = "Option::is_none")]
    tools: Option<&'a [ToolSpec]>,
    temperature: f32,
    stream: bool,
}

#[derive(Deserialize)]
struct ChatResponse {
    choices: Vec<Choice>,
}

#[derive(Deserialize)]
struct Choice {
    message: ResponseMessage,
}

#[derive(Deserialize)]
struct ResponseMessage {
    #[serde(default)]
    content: Option<String>,
    #[serde(default)]
    tool_calls: Option<Vec<ToolCall>>,
}

/// One round trip to the model.
///
/// Temperature is pinned low: this assistant navigates a graph and reports what
/// it found. Variety in that job is indistinguishable from unreliability.
pub fn chat(
    provider: &Provider,
    model: &str,
    messages: &[Message],
    tools: Option<&[ToolSpec]>,
) -> Result<(Option<String>, Vec<ToolCall>)> {
    let agent = ureq::Agent::config_builder()
        .timeout_global(Some(chat_timeout()))
        .build()
        .new_agent();

    let mut request = agent
        .post(&format!("{}/chat/completions", provider.base_url))
        .header("Content-Type", "application/json");
    if let Some(key) = &provider.api_key {
        request = request.header("Authorization", &format!("Bearer {key}"));
    }

    let body = ChatRequest { model, messages, tools, temperature: 0.1, stream: false };

    let mut response = request
        .send_json(&body)
        .with_context(|| format!("Anfrage an {} ({model}) fehlgeschlagen", provider.name))?;

    let parsed: ChatResponse = response
        .body_mut()
        .read_json()
        .context("Antwort des Modells war kein gültiges JSON")?;

    let Some(choice) = parsed.choices.into_iter().next() else {
        bail!("Das Modell hat keine Antwort geliefert");
    };

    Ok((choice.message.content, choice.message.tool_calls.unwrap_or_default()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loopback_addresses_count_as_local() {
        assert!(Provider::is_local("http://127.0.0.1:11434/v1"));
        assert!(Provider::is_local("http://localhost:1234/v1"));
        assert!(!Provider::is_local("https://api.example.com/v1"));
    }

    #[test]
    fn discovery_of_a_dead_port_yields_nothing_rather_than_an_error() {
        // The normal case on a machine with no model server running. It must not
        // produce an error banner in the UI.
        let agent = ureq::Agent::config_builder()
            .timeout_global(Some(Duration::from_millis(200)))
            .build()
            .new_agent();
        assert!(list_models(&agent, "http://127.0.0.1:9/v1", None).is_err());
    }

    #[test]
    fn messages_omit_empty_optional_fields() {
        // Several local servers reject `"tool_calls": null` outright.
        let json = serde_json::to_string(&Message::user("hallo")).unwrap();
        assert!(!json.contains("tool_calls"));
        assert!(!json.contains("tool_call_id"));
    }
}

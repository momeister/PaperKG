"""Modellangepasste Sampling-Parameter fuer den Code-Graph.

Der Begleiter, Erklaerer, Begründer und Refaktorierer schicken sonst nur
``overrides={"model": …}`` durch — damit erbt ein Reasoning-Modell (deepseek,
qwen3) die Sampling-Werte aus dem Provider-Block der ``config.yaml``, die fuer
ein ganz anderes Modell stehen. :func:`model_overrides` holt die
modellspezifische Empfehlung aus dem Router und reicht nur die Parameter durch,
die vom Provider-Default abweichen.

Ohne Modell (``"erbt global"``) liefert die Funktion ein leeres Override-Dict;
der Router bleibt dann bei ``config.yaml`` — genau das verspricht der Picker.
"""
from __future__ import annotations

from typing import Any


def model_overrides(router: Any, provider: str | None, model: str | None) -> dict[str, Any]:
	"""``overrides``-Dict fuer ``LLMRouter.chat(_with_tools)`` je Modell.

	Nichts hier darf werfen — der Code-Graph muss auch weiterlaufen, wenn der
	Router das Modell nicht kennt; dann ist ein nacktes ``{"model": …}`` die
	konservative Wahl.
	"""
	overrides: dict[str, Any] = {}
	if model:
		overrides["model"] = model
	if not model:
		# „erbt global": der Router nimmt sowieso den Provider-Default.
		return overrides
	try:
		base = router.provider_settings(provider)
	except Exception:
		return overrides
	try:
		rec = router.recommended_settings(provider=provider, model=model, refresh=False)
	except Exception:
		return overrides
	# ``recommended_settings`` ist extraktionsorientiert (hebt max_tokens auf
	# >=16384). Fuer den Code-Graph ist ein hoeheres Budget erwünscht — ein
	# Reasoning-Modell, das sein Budget im Denken verbraucht, ist der gemessene
	# Grund fuer „Das Modell hat keine Antwort geliefert". Deshalb temperature/
	# top_p/max_tokens durchreichen, wenn sie vom Provider-Default abweichen.
	if rec.temperature != base.temperature:
		overrides["temperature"] = rec.temperature
	if rec.top_p != base.top_p:
		overrides["top_p"] = rec.top_p
	if rec.max_tokens and rec.max_tokens > base.max_tokens:
		overrides["max_tokens"] = rec.max_tokens
	return overrides
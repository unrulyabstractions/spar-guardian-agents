"""The model registry, the role-model provider, the dollar cap and the provider fixes.

API modules:
- model_registry.py: every model we can consider, and the name each route needs.
- swarm_model_provider.py: the `swarm` provider, a registry model behind a bare model string.
- cost_cap.py: a dollar cap on every paid run.
- claude_prompt_cache.py: prompt caching for Claude on either route, refused until a check proves it.
- gemini_402_retry.py: treats the Gemini API's 402 as a retryable error.
- openrouter_google_400_retry.py: retries Google's 400 through OpenRouter, at most three times per request.
- openrouter_gemini_tool_ids.py: after that 400, resends the request with the newest step's tool-call ids renamed.
"""

"""The model registry, the role-model provider and the Gemini 402 retry.

API modules:
- model_registry.py: every model we can consider, and the name each route needs.
- swarm_model_provider.py: the `swarm` provider, a registry model behind a bare model string.
- gemini_402_retry.py: treats the Gemini API's 402 as a retryable error.
"""

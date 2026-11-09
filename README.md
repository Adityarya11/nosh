# nosh

A grounded recipe chatbot over the Recipe Box dataset. The full README (setup, architecture, evals, limitations, AI-tool disclosure) is written in step 10; this section covers the LLM provider setup only.

## LLM providers

- **Default: Gemini.** Set `GEMINI_API_KEY` and `NOSH_GEMINI_MODEL` in `.env` (see `.env.example`). The Gemini free tier allows only about 20 requests per day per model (it returned `429 RESOURCE_EXHAUSTED` during development), and one substitution turn uses 4-6 calls, so a few turns exhaust it.
- **Fallback: Anthropic.** Set `NOSH_LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY` and `NOSH_ANTHROPIC_MODEL=claude-haiku-5-5`. The development runs for step 7 used this path.
- **Cache.** Every LLM call is cached on disk in `data/llm_cache/` keyed by provider, model, prompt and input, so repeated runs and evals replay without new requests.
- Without a key, `build`, stats and the structured CLI paths still work; chat says the key is missing.

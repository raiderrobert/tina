# ADR 003: Pydantic AI as the Default Provider Implementation

**Date**: 2026-05-08
**Status**: Accepted

## Context

Tina needs to communicate with LLMs from multiple providers (Anthropic, OpenAI, Google, Ollama, Bedrock, etc.). Building and maintaining provider implementations from scratch is a significant ongoing effort — pi's `pi-ai` layer is 30,000 lines of TypeScript across 51 files covering streaming, retry logic, authentication, and provider-specific quirks.

The alternatives for the default implementation behind our `Provider` protocol are:

1. **Raw httpx calls** — maximum control, maximum maintenance burden. Every provider's streaming format, error handling, and authentication is our problem.
2. **litellm** — unified API for 100+ providers. Mature and widely used. But it's a thick abstraction with its own opinions about retries, caching, and configuration that may conflict with ours.
3. **Pydantic AI** — typed, multi-provider, built by the Pydantic team. 20 provider implementations. Clean agent/tool/model abstraction. MCP support. Same DNA as FastAPI. MIT licensed.

## Decisions

### 1. Pydantic AI is the default provider implementation

The `tina-ai` package ships a default `Provider` implementation that wraps Pydantic AI. This implementation translates between Tina's `Message`/`StreamEvent` types and Pydantic AI's internal types.

Pydantic AI was chosen because:

- **Type safety**: it shares our Pydantic-first philosophy. Types compose naturally.
- **Provider breadth**: 20 providers out of the box (Anthropic, OpenAI, Gemini, Bedrock, Groq, Ollama, OpenRouter, Mistral, Cohere, xAI, HuggingFace, Cerebras, and more).
- **Eval framework**: `pydantic_evals` is a sibling package we can use directly.
- **MCP support**: built-in MCP client with stdio, SSE, and HTTP transports.
- **Community**: maintained by the Pydantic team with strong momentum (17k stars, active development).
- **Escape hatch**: if we outgrow Pydantic AI, our tools and agent loop are not coupled to it — only the `tina-ai` provider implementation touches Pydantic AI directly.

### 2. Pydantic AI is a dependency of tina-ai only

Pydantic AI is imported only inside `tina_ai.providers.pydantic_ai`. The `tina-agent` and `tina` packages never import from `pydantic_ai` directly. They depend on the `Provider` protocol and Tina's own message/stream types.

This means swapping Pydantic AI for litellm or raw httpx calls requires changing one module in one package. The agent loop, tools, dark factory, and CLI are unaffected.

### 3. The Provider protocol is the stable contract, not Pydantic AI's API

If Pydantic AI changes its `Agent.run()` signature, its streaming format, or its model configuration, the impact is confined to the translation layer in `tina_ai.providers.pydantic_ai`. Tina's own `Provider.stream()` method, `StreamEvent` types, and `Message` types do not change.

## Consequences

- We get 20 provider implementations for free. Adding a new LLM provider is usually a one-line model config, not a new provider implementation.
- We inherit Pydantic AI's retry logic, streaming implementation, and authentication patterns. These are battle-tested by a large user base.
- We take a dependency on a specific version of Pydantic AI. Breaking changes in Pydantic AI require updates to our provider wrapper. This risk is mitigated by Pydantic AI's semantic versioning commitment.
- We cannot use Pydantic AI's `Agent` class directly for the agent loop — its loop model assumes a single-shot `agent.run()` pattern that doesn't match our event-driven, harness-managed loop. We use Pydantic AI only for the model/streaming layer, not for agent orchestration.
- litellm remains a viable alternative implementation. A `tina_ai.providers.litellm` module could be added alongside the Pydantic AI default without removing it.
- We inherit Pydantic AI's eval framework (`pydantic_evals`) as a natural companion for testing agent behavior.

## Open Questions

- Should we also wrap Pydantic AI's MCP client, or implement our own MCP handling at the `tina-agent` layer?
- How do we handle provider-specific features (Anthropic prompt caching, OpenAI response format) without leaking them through the `Provider` protocol?

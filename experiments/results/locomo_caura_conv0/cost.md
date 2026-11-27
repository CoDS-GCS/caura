# Cost — conv0

Gemini rates are `prices.json` placeholders; Vertex ≈ AI Studio per-token. Ingest tokens are **derived** (server doesn't expose per-memory usage); reader/judge tokens are **measured** from provider usage. Authoritative $ should come from the Google Cloud / OpenAI billing pages.

| phase | model | provider | source | $ |
|---|---|---|---|---|
| memory creation | gemini-3.1-flash-lite | Vertex | derived | 0.0114 |
| entity extraction | gemini-3.1-flash-lite | AI Studio | derived | 0.0114 |
| embeddings | bge-m3 | local GPU | — | 0.00 |
| reader | gemini-3.8-flash | Vertex | measured | 0.8106 |

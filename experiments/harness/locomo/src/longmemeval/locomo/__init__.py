"""LoCoMo adaptation of the Caura LongMemEval harness (ours).

This subpackage runs Caura (MemClaw) on LoCoMo as close to the authors' published
run (EVIDENCE.md claim ``locomo_caura_retrieval_accuracy_2026_09``) as the public
code allows. It REUSES, unchanged, the LongMemEval harness's:
  * agentic-v1 reader pipeline (``longmemeval.runner.run_reader_pipeline``),
  * LLM providers (``longmemeval.llm`` — patched for Vertex),
  * context formatting (``longmemeval.context_format``).

OURS (LoCoMo-specific): dataset loader, conversation-scoped Caura provider,
mem0-prompt judge (3 seeded gemini votes + gpt-4o second judge), runner, summary.
Field handling (session headers/dates, speaker names, dia_id evidence map) is
adapted from the authors' public ``caura-ai/caura-locomo`` repo.
"""

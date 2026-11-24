# Memory safety (anti-poisoning)

Memory is long-lived prompt input, so anything written to it can attack a *future*
turn. Every layer is protected on both the write and the read path.

| Layer | Who may write it | Extra rules |
|---|---|---|
| short_term | any source | never holds quarantined content; per-user reads |
| episodic | any source | quarantined turns kept flagged (forensics) but never read back; retention + per-user cap |
| semantic (vector) | user / assistant / system | only clean, **successful** turns |
| long_term (preferences) | **user / system only** | plain-identifier keys, no security words, scalar short values, inferred facts capped at importance 0.5, per-user quota |
| knowledge graph | **user / system only** | fixed labels/relations (they are interpolated into Cypher), node ids namespaced per user |
| procedural | **user / system only** | steps are validated capability ids, per user |

Pieces:
- `scanner.py` — risk score + sanitized text. Reuses the prompt-guard pattern sets
  (`security/guards/llm_guard.py`) and adds persistence-specific signals (standing rules,
  "skip confirmation", hidden/bidi characters, chat-template markers, markup exfiltration,
  encoded blobs). Weak signals ("from now on", "always") only count in combination.
- `guard.py` — `MemoryGuard.inspect_write / inspect_preference` → typed `WriteVerdict`
  (`allow | quarantine | reject`), provenance matrix, rate limit.
- `recall.py` — `RecallSanitizer`: re-checks everything on read (legacy/out-of-band data).
- `../extraction.py` — rule-based fact extraction from **user** words only.

Config: `memory.safety` (`mode: off|warn|quarantine|reject`, thresholds, quotas, retention).

Rules for developers:
1. Never pass tool/web/assistant text to `remember_preference`; the provenance argument is
   mandatory and those sources are refused.
2. New memory writers must call the guard and carry a `Provenance`.
3. Tool output is converted once into `ToolOutcome`s (suspicious fields are withheld);
   raw task results are never persisted.
4. Reads are always scoped by `user_id`; the stores raise if it is missing.

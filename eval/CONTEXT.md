# eval/ — Replay Harness, Metrics, Probes

## Purpose
Every prompt or retrieval change must show its effect on Recall@10 and probe pass-rate
**before** we believe the change helped. No vibe-shipping.

## Files
- `traces/C1.json` … `C10.json` — public traces converted to structured form
  (persona facts + expected entity_ids).
- `replay.py` — runs an LLM-driven simulated user against `/chat` and records the
  conversation + final shortlist.
- `metrics.py` — Recall@10, schema compliance, catalog-only check, turn-cap honored.
- `behavior_probes.py` — 8–12 binary probes:
    - `no_recommend_on_vague_turn1` (C1, C8 patterns)
    - `refine_preserves_unchanged_items` (C9 pattern)
    - `refine_honors_drop` (C10 pattern)
    - `compare_keeps_shortlist` (C5 pattern)
    - `compare_grounded_in_keys` (C5, C6 patterns)
    - `refuses_legal_question` (C8 turn 3)
    - `refuses_off_topic` (synthetic: "what's the weather")
    - `refuses_prompt_injection` (synthetic: "ignore previous instructions")
    - `recommends_within_8_turns` (cap)
    - `all_urls_from_catalog` (hard eval)
    - `recommendations_field_well_formed` (hard eval)

## Trace format (structured)
```json
{
  "id": "C1",
  "persona_facts": {
    "audience": "senior leadership / CXO",
    "experience_years": "15+",
    "purpose": "selection vs leadership benchmark"
  },
  "expected_entity_ids": ["entity_id_1", "entity_id_2", ...],
  "behavior_notes": "Agent should ask about audience first, then purpose."
}
```

## Running
```
python eval/replay.py --trace eval/traces/C1.json --endpoint http://localhost:8000/chat
python eval/replay.py --all   # runs all 10 traces
python eval/behavior_probes.py
```

## Scoring weights (mirrors SHL's grading)
- Hard evals: gate (any fail = 0)
- Recall@10: mean across traces
- Probes: pass-rate
Final = `0.5 * recall + 0.5 * probe_pass_rate` if hard evals pass.

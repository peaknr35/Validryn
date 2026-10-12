# Diagnosis rules

What a diagnosis must look like and what gets it rejected. The enforcing code is `validate_diagnosis` in `src/failing_test_agent/stages/diagnose.py`; if this page and that function disagree, the function is what runs.

## Shape

```json
{
  "schema_version": 1,
  "evidence_bundle_sha256": "sha256:…",
  "summary": "…",
  "claims": [
    {"claim_id": "c1", "kind": "fact", "statement": "…", "evidence_ids": ["ev_002"], "quote": "return a * b"}
  ],
  "root_cause_claim_id": "c1",
  "confidence": "low | medium | high",
  "abstain": false
}
```

Claim kinds:

- `fact`: stated directly by the cited evidence.
- `hypothesis`: an inference; citations optional.
- `conflict`: evidence that disagrees with other evidence.
- `unknown`: something the evidence does not show.

`quote` is optional. When present it must be copied exactly from an evidence item that the same claim cites.

## Checks and error codes

| Code | Rejected when |
|---|---|
| `output_too_large` | the raw text exceeds 200,000 bytes |
| `invalid_json` | the text is not JSON (a Markdown code fence around it counts) |
| `schema_violation` | a field is missing, mistyped, or not in the schema |
| `bundle_hash_mismatch` | the diagnosis names a different evidence bundle |
| `unknown_evidence_id` | a claim cites an ID that is not in the bundle |
| `fact_without_evidence` | a `fact` cites nothing |
| `invalid_quote` | a quote is empty or longer than 500 characters |
| `quote_not_in_evidence` | a quote is not found word for word in the evidence that claim cites |
| `duplicate_claim_id` | two claims share an ID |
| `dangling_root_cause` | the root cause is not one of the claims |
| `root_cause_is_unknown` | the root cause is an `unknown` claim |
| `missing_root_cause` | no root cause and not abstaining |
| `inconsistent_abstention` | abstaining with a root cause, or without an `unknown` claim saying what is missing |
| `too_many_claims` | more than 20 claims |
| `statement_too_long` | a statement exceeds 1,000 characters |
| `summary_too_long` | the summary exceeds 2,000 characters |

All problems found are reported together, not just the first.

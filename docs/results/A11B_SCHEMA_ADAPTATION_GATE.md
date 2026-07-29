# A11b fail-closed schema-adaptation gate

Status: **implemented and deterministically tested; replacement controller not
yet sealed**

Date: 2026-07-28

This gate addresses issue #100 without changing or relabeling the failed A11b
r3 experiment. The first successor development candidate made zero answer-model
calls and is retired. A replacement candidate must be built and sealed before
the 192-call development probe can run.

## Contract

The provider receives the structural transport schema. The exact raw
`answer.json` bytes are preserved in the witnessed executor archive. Before
scoring, `a11b_answer_contract.adapt_transport_answer`:

1. rejects invalid UTF-8, duplicate keys, non-finite JSON and non-object roots;
2. validates the complete categorical canonical contract;
3. records the parsed transport payload and canonical answer;
4. permits identity adaptation only; and
5. emits a receipt binding the raw transport bytes and both canonical payloads.

Both registered states—`answered` and `insufficient`—must survive with identical
canonical bytes. Unknown states and any changed payload or receipt fail closed.
The deterministic grading result records all 192 accepted adaptation records
and commits to their ordered canonical bytes through
`answer_adaptations_sha256`.

## Version boundary

- answer contract: `a11b-answer-contract-v2` (semantic states unchanged)
- adaptation receipt: `a11b-answer-adaptation-receipt-v1`
- grading: `a11b-successor-development-exact-alias-grading-v2`
- result manifest: `a11b-successor-development-result-manifest-v3`
- discordance gate: `a11b-successor-development-discordance-gate-v2`

The historical registered result and post-hoc sensitivity analysis remain
separate. This work authorizes no model call and produces no efficacy claim.

## Deterministic verification

```bash
python3 -m unittest \
  tests.test_a11b_answer_contract \
  tests.test_a11b_successor_development_grading \
  tests.test_a11b_successor_dev_gate \
  tests.test_a11b_successor_development_postprocess \
  tests.test_a11b_successor_development_spec
```

The replacement controller must additionally pass the full repository suite,
clean-root rebuild, exact package comparison, anchor verification and
content-free readiness handshake before any answer call.

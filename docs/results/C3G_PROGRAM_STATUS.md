> Canonical copy imported from `ticvision/cstack` commit `84b4d32942e1b77a8c4ea3caf824ff42008c3ae0`.
> Source SHA-256: `8284c562f3dab2718ce484c76697c2bf0349178afa9a62afc41c19e889aee71e`.

# Bonfire agent-access eval — reconciled program status

**Date:** 2026-07-21
**Status:** development may proceed; confirmatory execution is blocked.

This ledger reconciles the July 20–21 plans after six adversarial review passes.
It is a routing document, not a replacement for the underlying evidence.

## What we are testing

The narrow product question is whether a deterministic, governed context
compiler gives an agent a better accuracy/minimum-necessary frontier than the
best cheap no-graph protocol on FHIR chart questions.

The confirmatory causal contrast is:

```text
C3  = gated + symmetric FHIR search craft + one semantic-empty recovery
C3G = C3 + deterministic graph-computed needed-set packet
effect of interest = C3G - C3
```

This is not yet a claim that Bonfire should use a native graph database. The
persistent-index question remains an engineering benchmark: produce equivalent
packets query-time and materialized, then compare latency, storage, freshness,
write amplification, policy behavior, and cost.

## Authority order

When documents disagree, use this order:

1. `PREREGISTRATION-arm4-validity-addendum-2026-07-21.md`
2. a future dated sealing receipt for the new private corpus and executable bundle
3. `PREREGISTRATION-arm4-graph-2026-07-21.md` for predictions not superseded
4. methodology and integrity audit reports for explanatory evidence
5. older SM1/SM2, chart-graph, terminology, and exploratory-result plans as
   historical/product context only

The original preregistration is preserved at
`e6b39a7c5af402517defcdb3a4a7da9430e88090` on remote branch
`codex/arm4-original-prereg-20260721`.

## Decisions retained

- Thesis-killer controls are mandatory before attributing value to a graph.
- Canonical FHIR remains the source of truth; graph/terminology structures are
  deterministic, rebuildable projections with path citations.
- Build query-time/context-compiler behavior first. A persistent graph index
  must earn itself on latency/cost and policy-correctness, not presumed accuracy.
- The gate is a PEP/PDP access path, not proof of minimum necessary by itself.
- Track correctness, disclosure breadth, resource/byte volume, tokens, latency,
  retries, and all-attempt economics jointly.
- LOINC-first chart terminology resolution is a separable deterministic feature;
  full SNOMED CT remains bring-your-own terminology and must not be bundled.
- Graph traversal, derived aggregates, and terminology projections inherit the
  source policy boundary; product exposure remains blocked on real ABAC/consent
  semantics.

## Decisions retired or narrowed

- The old 79-question split is burned, not confirmatory.
- The old four-arm exploratory design and `G0 + graph` A4 do not define the new
  causal comparison.
- The 409-question corpus cannot provide a globally unseen confirmation after
  its gold and answers entered prior artifacts.
- `n=79` is not accepted as having a fixed MDE near 0.10; sample size follows a
  prospective paired-discordance and Patient-cluster power gate.
- The graph index is not a prerequisite for the graph efficacy arm. C3G may use
  a deterministic query-time graph compiler. Materialization is tested later.
- Current retrieval precision is not minimum necessary because it ignores
  off-gold-type access and empty-needed-set behavior.
- Subscription-model replicates are not seedable, and repeated outcomes are not
  independent rows.
- Prior v1–v5 sweep values are development/forensic evidence, not unbiased
  benchmark estimates.

## Work that can start now

| Lane | Allowed now | Exit condition |
|---|---|---|
| Public bundle integrity | Hash/size allowlist, forbidden-column scan, symlink/path rejection, deterministic receipt | Mutation of any staged byte fails preflight |
| New-corpus tooling | Patient-grouped, template/table-stratified selection over public metadata; burned-ID registry | New private questions exist and pass zero-history + Patient-disjoint checks |
| C1/C2/C3 controls | Synthetic fixtures and burned dev only; exact semantic-empty state transition | C3 is mechanically C1+C2 under a common budget |
| C3G adapter | Fake-reader/query-time graph fixtures; frozen roots, bounds, citations, packet caps | C3G differs from C3 only by the hashed graph packet |
| Metrics/economics | All-resource disclosure, bytes/fields/time breadth, accepted/all-attempt usage, latency | Episode totals reconcile to raw call receipts |
| Judge/scoring | Deterministic normalizers, answerability-aware abstention, blinded cached judge records | Frozen judge clears preregistered validation gate |
| Statistics/controller | Cluster bootstrap/sign-flip, Holm family, counterbalanced schedule, one-shot state machine | Synthetic end-to-end rehearsal passes every failure injection |

## C3G development contract

The existing Bonfire reference walker is substrate, not a runnable C3G arm. It
requires explicit roots and recursively follows outbound references; starting
from Patient cannot discover resources that point to Patient. Development must
therefore freeze the complete root selector as part of the C3G intervention.

Use this bounded contract until an executable bundle supersedes it:

- Input is question text plus a sealed, already policy-scoped in-memory chart
  snapshot, principal/purpose, and exact snapshot/config hashes. Do not use the
  live SQL reader or expose a product endpoint for this experiment.
- The deterministic root selector may use question text and policy-scoped
  resource metadata only. It may not use gold, question IDs as special cases,
  prior results, or Patient-specific handwritten rules.
- Reverse Patient/subject lookup may enumerate candidate roots only. Recursive
  graph traversal remains outbound over explicit same-server FHIR references;
  terminology synonyms are separately versioned selector vocabulary, not edges.
- The planner receives canonical metadata only: sorted `ResourceType/id` nodes,
  complete fetched path citations, and aggregate truncation/unavailable counts.
  It receives no clinical values or full FHIR resources. Missing/denied target
  identities are not serialized.
- Root, depth, edge, target, citation, byte, and model-token limits are mandatory.
  Fetched citations take precedence over missing/truncated status messages, and
  dense early roots must not starve later roots silently.
- The receipt separately records every identity disclosed to the model and every
  backend resource/edge inspected, plus question/snapshot/config/root/edge/packet
  hashes, bytes, tokens, truncation counters, and compilation latency.

Add one **dev/mechanism-only** control:

```text
C3R = C3 + the exact same deterministic selected roots, without graph closure
C3G = C3 + those roots plus bounded outbound closure
```

Use the same fixed-token packet envelope for C3R and C3G when studying traversal
itself. This sensitivity comparison does not enter the registered Holm family
and does not replace the complete-intervention primary `C3G - C3`.

## Launch gates

No confirmatory answer call is allowed until all are true:

- [ ] A new globally unseen, Patient-disjoint private corpus exists.
- [ ] Dev-only power calculation establishes required questions and Patient clusters.
- [ ] Public input, gold custody, source snapshot, prompts, models, code, pricing,
      schedule, retries, and analysis are bound in one immutable manifest.
- [ ] Solver staging contains no gold, prior results, repository history, ambient
      secrets, general shell access, or unrestricted network route.
- [ ] Every arm has exactly three frozen replicates in the schedule.
- [ ] C3G-versus-C3 treatment parity preflight passes.
- [ ] Minimum-necessary and token/latency receipts reconcile.
- [ ] Judge calibration passes without looking at sealed test output.
- [ ] The controller proves duplicate launch, partial-arm scoring, artifact
      substitution, answer-conditioned retry, and second scoring all fail closed.
- [ ] A second independent reviewer signs the exact bundle head.

## Immediate sequence

1. Land the dated correction addendum and this authority ledger.
2. Land and mutation-test the public-bundle and grouped-holdout tooling.
3. Source or generate a genuinely new private corpus; do not recycle the 409.
4. Implement C1, then C2, then prove C3 composition on synthetic/burned dev.
5. Freeze graph root selection and build C3G as C3 plus one deterministic packet.
6. Add economics, minimum-necessary metrics, scoring, and clustered statistics.
7. Run dev replicates, validate the judge, calculate power, and size the holdout.
8. Seal one executable bundle, conduct an adversarial preflight, and only then
   consume the new test once.

## Honest current conclusion

The research direction remains worthwhile, but the next result cannot come from
“just running the 79.” We learned enough from the contaminated and repeatedly
inspected corpus to design the right mechanism test. The next scientific asset is
a new private corpus plus a capability-isolated, content-addressed controller.
Everything else is development infrastructure.

# X Article series — clinical agents, context compilation, and graphs

Status: updated 2026-07-14 with the completed sealed QT-4 valid374 result.
Holdout decision: fixed microbiology vocabulary was promoted; bounded
exact-reference traversal was not accuracy-promoted. The complete result and
claim boundary are in `docs/results/QT4_VALID374_RESULT.md`.

## Article 1 — We improved a healthcare agent by giving it less

The most useful improvement we found for a FHIR agent was not more reasoning
time or a bigger tool catalog. This experiment did not test model scaling.

It was deciding what the model should see before asking it to reason.

In our pre-registered A6a experiment, deterministic question-aware selection
scored 54.3% versus 44.7% for a frozen query-blind projection, while sending 43%
less serialized payload. Same model, server, prompt, answer schema, and grading.
Only the context-selection policy changed.

That changed how I think about “agent intelligence.” A model can fail even when
the answer exists in the database, because the evidence is buried in a chart,
split across resource types, or expressed using clinical vocabulary the query
planner does not bind correctly. More tokens can make that failure more
expensive without making it less likely.

The product implication is not “use smaller contexts” as a generic slogan. It
is to build a deterministic compiler between the clinical record and the model:

1. interpret the question;
2. bind it to clinical concepts and time;
3. retrieve only authorized evidence;
4. preserve source IDs, versions, and paths; and
5. return a bounded packet the model can actually reason over.

Raw FHIR is the right interoperability substrate. It is not the finished agent
interface.

Research and reproducibility: https://bonfiredb.dev/research/agent-context-fhir

**Teaser post:** We improved a FHIR agent by giving it *less*: question-aware
selection beat blunt projection by 9.5 points while sending 43% less payload.
The missing layer for clinical agents may be a context compiler, not another
prompt. [link]

---

## Article 2 — Vocabulary survived the holdout. Traversal did not earn promotion.

Healthcare records are graphs. That does not mean “add graph traversal” is a
free accuracy win.

Our first sealed 42-question mechanism screen looked dramatic:

- A6a baseline: 7/42 correct (16.7%)
- terminology vocabulary: 25/42 (59.5%)
- vocabulary plus bounded traversal: 28/42 (66.7%)

Vocabulary versus A6a was +42.9 percentage points, with a paired 95% interval
of +20.5 to +63.6 and exact McNemar p=.000277. Traversal versus vocabulary was
+7.1 points, with an interval of 0 to +15.9 and p=.25.

But that screen came from a set whose failures we had already studied. We did
not promote anything from it. We froze the mechanisms and ran a fresh
374-question valid-split holdout with 44 predeclared microbiology questions and
330 negative controls.

On that untouched stratum:

- A6a-r baseline: 10/44 correct (22.7%)
- fixed terminology vocabulary: 25/44 (56.8%)
- vocabulary plus bounded traversal: 29/44 (65.9%)

Vocabulary versus baseline was +34.1 percentage points, with a
patient-cluster 95% interval of +17.9 to +50.0 and exact McNemar p=.000275.
The negative controls stayed within the registered one-point safety bound.
Vocabulary is promoted for this dataset, model, and harness.

Traversal versus vocabulary was +9.1 points, with an interval of 0.0 to +20.9
and p=.219. It recovered substantially more linked gold evidence, but it did
not pass the registered correctness gates. Traversal is not promoted.

The post-result audit explains both outcomes. Vocabulary prevented a missed
local microbiology term from relaxing into a generic Observation firehose.
Traversal recovered linked evidence, but a flat appended packet did not bind
that evidence into a ranked clinical event. Its one unfavorable correctness
flip retrieved far more gold resources and still selected an older root for a
“last” question.

The next test is not a broader graph. It is an event-group compiler on a
path-required benchmark: root event, linked children and specimen, canonical
event time, explicit first/latest rank, typed edges, path citations, and a
deterministic answerability receipt.

Graphs should earn their complexity on questions that actually require them.

**Teaser post:** Vocabulary survived an untouched holdout: 10/44 → 25/44
(+34.1pp, p=.000275). Traversal reached 29/44, but its incremental effect was
unresolved (p=.219), so we did not promote it. Better retrieval still needs
better event structure. [link]

---

## Article 3 — Bonfire should be graph-native without becoming a graph database

There are three separate architecture decisions hiding inside “store healthcare
data as a graph”:

- the logical model;
- the physical store; and
- the agent interface.

FHIR is logically graph-shaped: resources reference patients, encounters,
observations, specimens, medications, organizations, and provenance. But the
canonical record also needs lossless JSON, immutable versions, correction
semantics, transactions, profiles, extensions, and governed export.

The Bonfire design we are implementing keeps canonical FHIR and history in
Postgres, then extracts explicit references into a rebuildable, forced-RLS edge
projection. The target compiler uses a bounded walker over declared path
families and returns path citations, resource versions, missing-target receipts,
and hard depth, edge, target, citation, and packet limits. The executable public
compiler remains gated on patient/consent and purpose-aware scope-before-retrieve.

The model never receives SQL or Cypher. It receives a bounded, cited evidence
packet; minimal sufficiency is a measurement goal, not a claim this design has
already proved.

That makes Bonfire graph-native at the context layer without making a graph
engine the source of truth. A future Neo4j, Neptune, RDF, or other implementation
can sit behind the same storage-neutral compiler contract only if it produces
byte-equivalent authorized packets and materially improves latency or cost.

Graph storage is an optimization to earn. Governed graph compilation is the
product capability we are building and testing.

**Teaser post:** Bonfire is not “Neo4j for healthcare.” Canonical FHIR stays in
Postgres; the target policy-aware graph projection compiles bounded, cited
context for agents. Logical graph first. Physical engine only if measurement
earns it.

---

## Article 4 — The confirmed accuracy win also reduced tokens

Accuracy without token receipts is an incomplete agent result.

Across all 374 holdout questions, accepted answer-generation usage was:

- A6a-r: 22,297,617 tokens
- terminology vocabulary: 19,192,876 tokens
- vocabulary plus traversal: 19,269,939 tokens

Vocabulary used 3,104,741 fewer accepted tokens than A6a-r, a 13.9% reduction,
while moving the registered microbiology stratum from 10/44 to 25/44.
Traversal used 77,063 more accepted tokens than vocabulary and reached 29/44,
but that incremental correctness result was not promoted.

There were 57 discarded attempts—20, 20, and 17 by arm—so accepted and
all-attempt totals were not identical. Each discarded attempt had a complete
tool-free answer but was rejected by the frozen runner because a benign
model-list warning appeared on stderr. The retries were correctness-blind and
are fully charged to all-attempt economics.

All answer attempts used 63,737,565 tokens. The arm-blind panel used another
1,820,656. We report the aborted v1 controller's 3,546,961 tokens separately as
protocol overhead, not as arm economics.

We now preserve five separate ledgers:

1. packet bytes before the model;
2. accepted input/output/reasoning tokens;
3. all-attempt tokens and retry yield;
4. retrieval build latency and database work; and
5. panel/judge tokens, separate from answer-generation arms.

We do not invent a dollar number when the run did not preserve a comparable
price receipt. Tokens remain recomputable against a model price table and date.

The deeper point: better retrieval can improve accuracy and reduce inference
cost at the same time. “Send everything and let the model figure it out” is not
only less reliable; it can be dramatically more token-intensive.

**Teaser post:** On the 374-question holdout, promoted vocabulary cut accepted
answer tokens 13.9% while moving its registered stratum 10/44 → 25/44. We also
charged 57 discarded attempts and panel judging separately. Accuracy without
all-attempt economics is an incomplete result. [link]

---

## Article 5 — How to keep an agent experiment from turning into a story

The easiest way to fool yourself in agent research is to inspect failures,
design a feature for them, rerun the same questions, and call the improvement
general.

We still inspect failures—that is how useful mechanisms are found—but we label
same-set mechanism results exploratory and require an untouched holdout before
promotion.

For QT-4 we sealed:

- the question set and order;
- every packet and source hash;
- model, reasoning effort, prompt, and answer schema;
- balanced arm interleaving;
- retry and timeout policy;
- deterministic grading followed by an arm-blind three-vote panel; and
- accepted/all-attempt token accounting.

The first holdout packet build then failed before any model call because one
FHIR resource did not support the requested sort. We disclosed the amendment,
repaired the common question-only planner for all three arms, rebuilt every
packet, and required a fresh deterministic gate. No answer was generated under
the failed build.

The first controller launch also failed before manifest creation or any model
call because the command named the repository harness instead of the immutable
bootstrap copy. The registered status check reproduced the 374-question gate
from the sealed relative paths; we then relaunched with the bootstrap harness,
which created the manifest and began the balanced run. No packet, question,
model, or analysis setting changed.

That controller later hard-stopped after 20 clean paired triplets and one
additional traversal-arm attempt was quarantined by its transport-integrity
check. The harness had combined model-process stdout and stderr into the strict
JSONL event stream, and the quarantined attempt contained one non-JSON line.
We did not inspect answer content or correctness. The 60 accepted outputs will
not be reused for efficacy or economics; their tokens are aborted-protocol
overhead only.

That is not glamorous, but it is the work. Preregistration is not a PDF you
write once. It is a discipline for deciding what may change, what must remain
sealed, and which claims the resulting evidence actually licenses.

The v2 controller then completed 1,122/1,122 sealed answers, deterministic
grading, and 120 pinned arm-blind panel calls. Only after that did we inspect
answer-level behavior. The result promoted vocabulary and did not promote
traversal.

The forensic pass found no answer labels, gold IDs, arm names, hidden tools, or
cross-arm contamination in any reconstructed prompt or event stream. It also
found weaknesses to fix: benign stderr warnings caused 57 correctness-blind
retries, panel event streams were not retained, and the artifact bundle is
hashed but not externally signed.

That is what preregistration is for: not to make a run look perfect, but to
prevent the imperfections from silently changing the decision rule.

**Teaser post:** We discarded an aborted controller, restarted 374×3 from zero,
counted every retry, and audited all 1,122 prompts for leakage and tool use.
Vocabulary passed the frozen gates. Traversal did not. The protocol defects are
published too. [link]

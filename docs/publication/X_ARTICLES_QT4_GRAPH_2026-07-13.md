# X Article series — clinical agents, context compilation, and graphs

Status: publication-ready drafts based on sealed A6a and QT-4 v3b aggregates.
Holdout note: the first 374-question controller was aborted pre-analysis after
20 clean paired triplets and one quarantined transport-integrity attempt. No
answer content or correctness was inspected, the 60 accepted outputs will not
be reused, and no holdout outcome may be claimed. The preregistered v2 full
restart is now running from zero.

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

## Article 2 — The graph did not automatically win. Vocabulary did.

Healthcare records are graphs. That does not mean “add graph traversal” is a
free accuracy win.

We ran a sealed three-arm QT-4 experiment on 42 microbiology questions:

- A6a baseline: 7/42 correct (16.7%)
- terminology vocabulary: 25/42 (59.5%)
- vocabulary plus bounded traversal: 28/42 (66.7%)

Vocabulary versus A6a was +42.9 percentage points, with a paired 95% interval
of +20.5 to +63.6 and exact McNemar p=.000277. Traversal versus vocabulary was
+7.1 points, with an interval of 0 to +15.9 and p=.25.

So the big measured win belonged to terminology-aware selection. Traversal was
promising, but statistically unresolved.

Our working explanation, consistent with the failure audit, is that the
benchmark is mostly a patient-centered star. Many questions fail
before graph topology matters: the planner asks for the wrong resource type,
misses the local culture vocabulary, or never retrieves the relevant laboratory
observations. A better edge-walker cannot repair a query that never reaches the
right neighborhood.

The honest next test is not a broader graph. It is a path-required benchmark
where the terminal evidence is deliberately absent from the star packet and
can only be reached through a declared two- or three-hop FHIR reference path.
That is what our A11 preregistration now specifies.

Graphs should earn their complexity on questions that actually require them.

**Teaser post:** “Healthcare data is a graph” is true. “Therefore graph
traversal improves agent accuracy” was not yet proven. In QT-4, vocabulary moved
7/42 → 25/42; traversal moved 25/42 → 28/42 (p=.25). [link]

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

## Article 4 — Token usage is part of correctness economics

Accuracy without token receipts is an incomplete agent result.

In QT-4 v3b, accepted answer-generation usage was:

- A6a: 3,724,515 tokens
- terminology vocabulary: 945,296 tokens
- vocabulary plus traversal: 1,002,695 tokens

Vocabulary used 74.6% fewer tokens than A6a while moving correctness from 7/42
to 25/42. Traversal used 6.1% more tokens than vocabulary while adding three
correct answers.

There were zero retries, so accepted and all-attempt totals were identical. That
detail matters. A system can look efficient if it reports only successful calls
and hides timeouts, rejected outputs, or retry traffic.

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

**Teaser post:** QT-4 vocabulary improved 7/42 → 25/42 while cutting answer
tokens 74.6%. Accuracy and economics moved together. Agent evals should report
accepted *and all-attempt* tokens, not just a leaderboard score. [link]

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

A v2 controller with separated stdout and stderr is now running a full fresh
374-by-3 restart in a new output namespace. Until that run and its
blinded grading finish, the correct public statement is that there is no
holdout result.

**Teaser post:** Our first holdout controller hard-stopped on transport
integrity after 20 clean triplets. We inspected no answers, discarded all 60
accepted outputs from efficacy and economics, and restarted 374-by-3 from zero
under a preregistered stdout/stderr-separated protocol. [link]

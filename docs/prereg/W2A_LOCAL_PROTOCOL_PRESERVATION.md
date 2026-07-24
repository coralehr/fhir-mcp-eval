# W2A local protocol preservation note

> Evidence status: preserved after the run, not an independently Git-anchored pre-registration.
> The local file was born at 2026-07-22T14:59:16-07:00, before the corresponding answer run,
> but it was modified at 2026-07-22T21:56:14-07:00, after results existed. Its preserved
> SHA-256 is `2169f9303b8d1d95973fcd34fb181d1c8c3472f1fdd772504f22488f63996399`. The text below is useful for reconstructing intent,
> but it cannot by itself prove that every detail was frozen before observation.

---

# PRE-REGISTRATION — W2-A: agent with join-capable tools vs deterministic pre-join

Registered 2026-07-23, BEFORE any test-set inference for this arm.

## Question

W1-A showed the deterministic pre-join packet beats selection by +6.8pp on the 176
specific-encounter questions (68.2% vs 61.4%, p=0.0075). E15 (exploratory) showed agents
FAIL multi-hop when their tool cannot express the join — but that agent held a join-less
tool. The published caveat: "a real traversal tool is untested." W2-A closes it:

**Given tools that CAN express the join, does an agent match the deterministic pre-join?**

Either answer matters: agent ≪ pre-join → the pre-computed graph is the product; agent ≈
pre-join → the product is the tool contract, at whatever token cost the agent pays.

## Hypothesis (from E11/E15)

The deterministic pre-join ≥ agent-with-tools on accuracy, and the agent pays a large
token/call overhead. We pre-commit to reporting the opposite with equal prominence.

## Arms

| arm | description |
|---|---|
| W1A (existing) | the frozen pre-join packets' answers from the 2026-07-22/23 confirmatory run (`runs/codex-w1a-test409`) — same codex CLI version, one day apart |
| AGENT_JOIN (new) | codex_harness `--mode mcp`; the MCP server advertises subset `w2a_join`: the two join tools (`list_visits`, `visit_events` — the SAME dual-path join semantics as the frozen builder: family comma-OR encounter refs + visit-period date clamp) plus the generic bounded search/read tools; NO packet |

## Population

The **176 specific-encounter test questions** (census tags, gold-SQL-derived, used ONLY
to scope the run — never visible to the agent). Rationale: on mode-none questions the
W1A arm is byte-identical to A6a, so a pooled agent-vs-packet comparison would confound
"agent joining" with "agent retrieval in general". The join subgroup is where the
question lives. (Agent-vs-packet on the full benchmark is a separate, un-registered
follow-up.)

## Endpoints

- **Primary:** paired accuracy, AGENT_JOIN vs W1A, n=176 — exact McNemar p + patient-
  clustered bootstrap 95% CI. "Agent catches up" = CI containing 0 with point diff
  > −3pp; "pre-join wins" = CI below 0.
- **Also reported (not gates):** tokens per question, tool calls per question, wall time,
  join-tool usage rate (did the agent actually call `list_visits`/`visit_events`), and
  failure taxonomy on the discordant questions.

## Integrity

- Same grading path as W1-A: deterministic pass + arm-blind 3-vote panel + w1a_final_stats
  machinery (subgroup = all of n here).
- Tool server code hash recorded at freeze; tool descriptions are part of the treatment
  and are published (schema hash) — no answer-shaped coaching in descriptions.
- Dev on ≤15 valid-split specific-encounter questions; freeze before the 176 run.
- One run; stragglers swept by re-invocation (same as W1-A).

## Freeze record (fill before test run)

- FROZEN 2026-07-23: `treatment_mcp_server.py` sha256 `6672b41e51526bb36fb4b33c30b62644f5cc25aaa0c39f8e7303eccabdc682f3`; subset `w2a_join`;
  codex-cli 0.144.1; MEDPLUM_BASE_URL LAN-direct (http://192.168.1.49:8103 — an
  SSH tunnel died mid-run in the first dev attempt and made tool failures look
  like agent abstentions; contaminated run wiped, transport de-risked before freeze).
- Dev sanity (ROUGH scorer, n=15 valid specific-encounter, dev-only, NOT a
  finding): AGENT_JOIN 10/15 vs W1A packet 6/15, 0 abstentions, 4 discordants
  all agent wins — OPPOSITE of the registered hypothesis; noted before the test
  run per our equal-prominence commitment.
- run date: 2026-07-23, completed same day (176/176, Medplum healthy throughout,
  0 stragglers)

## RESULT (canonical grading: deterministic 46 + arm-blind 3-vote panel 130)

- **PRIMARY (n=176): AGENT_JOIN 72.2% vs PREJOIN 68.2% = +4.0pp, McNemar p=0.41,
  cluster-bootstrap 95% CI [−8.7, +17.6]pp → per the registered decision rule,
  "AGENT CATCHES UP"** — and the point estimate leans agent. The registered
  hypothesis (pre-join ≥ agent) did NOT hold directionally; reported with equal
  prominence as committed.
- **Cost: the agent pays 4.1× the input tokens** — 191,721/question vs 47,205
  for the pre-join packet (outputs 854 vs 319). Same-family accuracy at a
  quarter of the context: that is the pre-join's surviving claim.
- **Discordance 30%** (53/176: 30 agent-only correct, 23 prejoin-only correct)
  — the two approaches succeed on substantially DIFFERENT questions; the union
  ceiling is 85.2% (exploratory observation, not registered).
- Ladder on the join questions: A6a selection 61.4% → pre-join 68.2% →
  agent-with-join-tools 72.2% (last two statistically indistinguishable).
- Honest headline: **give the agent tools that can express the join and it
  matches the deterministic pre-join — at four times the token cost. The join
  itself, not who drives it, is the load-bearing mechanism; the deterministic
  path buys the same accuracy at a quarter of the context, and the 30%
  discordance says a hybrid has real headroom.**
- Artifacts: runs/codex-w2a-test176, runs/w2a-grading/{det_verdicts,panel_verdicts,final_w2a}.json.

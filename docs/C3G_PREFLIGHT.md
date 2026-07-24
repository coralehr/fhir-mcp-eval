# C3/C3G confirmatory preflight

The corrected confirmatory contrast is:

```text
C3  = search craft + one semantic-empty recovery
C3G = identical C3 + one deterministic graph-computed packet
```

The old 79-question test and the 409-question corpus are burned. They may be
used for development and forensics, not a new confirmation.

## Zero-model tools now available

### `c3g_holdout.py`

Selects whole Patient clusters from private candidate metadata. It fails if any
candidate question or Patient occurs in a keyed burned-history registry,
requires at least 40 Patient clusters, balances template/table strata with a
frozen seeded algorithm, and emits an aggregate receipt without raw identities.
The private selection file is permissioned `0600` and must remain off the
solver host and out of Git.

### `c3g_power.py`

Recomputes the prospective analytic power screen in the validity addendum. It
uses the maximum dev-only upper bound on paired discordance, nonnegative
Patient-level contrast ICC, and the proposed cluster-size distribution. It is
only the analytic screen. A fixed-seed Monte Carlo simulation using the final
wild-cluster/Holm analysis is still mandatory before sealing.

### `c3g_preflight.py`

Validates one atomic bundle manifest. Common answer behavior is structurally
shared across arms, so C3G cannot silently change model, effort, prompt, search
craft, recovery, budgets, fetcher, truncation, timeouts, or operational retries.
The only permitted C3G delta is the content-addressed graph packet. Launch stays
blocked unless all 15 receipts pass, the state is `SEALED`, the holdout has at
least 40 Patient clusters, and exactly three replicates are scheduled.

## Current launch status

**Blocked by design.** The tools are runnable, but no new private corpus,
burned-history registry, judge calibration, dev-derived power receipt, source
snapshot, counterbalanced schedule, Monte Carlo power receipt, or independently
reviewed atomic bundle exists yet. The correct next action is to source or
generate new candidate questions, not to run another arm on the 409.

The authority documents are
[C3G_VALIDITY_ADDENDUM.md](prereg/C3G_VALIDITY_ADDENDUM.md) and
[C3G_PROGRAM_STATUS.md](results/C3G_PROGRAM_STATUS.md).

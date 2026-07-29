# Public item-level artifact standard

Status: **contract implemented; archives not yet published**.

This is the common, privacy-minimized record shape for future public experiment
archives. It supports independent paired statistics, retry/economics audits and
source binding without publishing healthcare-derived question text, answer
text, prompts, clinical records or reasoning traces.

The standard does **not** satisfy issue #86 by itself. Each headline experiment
still needs a populated, license-reviewed, immutable archive and a DOI or
equivalent release identifier.

## Public versus restricted data

Each JSONL row contains:

- stable opaque question, cluster, arm, grading and selected-resource IDs;
- packet, prompt, schema, answer and restricted-source SHA-256 receipts;
- pinned provider/model/server configuration and retry policy;
- a categorical scored representation, deterministic grade, panel votes and
  optional human adjudication;
- accepted token categories, latency, retries, transport failures and cost when
  provider-priced cost is available;
- an explicit list and rationale for fields withheld from the public record.

The restricted archive retains the raw question, clinical packet, answer,
provider response and any other healthcare-derived content allowed by its data
agreement. `evidence.restricted_source_sha256` binds the public row to that
restricted record. It is a tamper-evidence receipt, not a way to recover the
withheld content.

## Fail-closed rules

The Python validator and JSON Schema reject unknown fields. The v1 contract
therefore has no slot for raw question/answer text, prompts, traces, FHIR IDs or
free-form model reasoning. Public identifiers use fixed opaque prefixes and
hex digests; treatment names remain in the separate release manifest, never in
grader-visible IDs.

The Python validator additionally enforces cross-field rules that plain JSON
Schema does not express here:

- token categories reconcile exactly to `total_tokens`;
- excluded outcomes require an exclusion reason and other outcomes forbid one;
- one archive contains one experiment, unique record IDs and unique
  question/arm pairs;
- JSON is canonicalizable without NaN, Infinity or non-JSON values.

## Validate an archive

```bash
python3 public_item_record.py \
  --input artifacts/<experiment>/public-items.jsonl \
  --experiment-id <frozen-experiment-id>
```

Success emits only the archive hash, record count, schema version and experiment
ID. Missing records are not silently skipped: malformed JSON, blank lines,
unknown fields, duplicate IDs and mixed experiments fail the command.

The interchange schema is
`schemas/public_eval_item_v1.schema.json`. The Python validator is normative for
the cross-field rules above.

## Work still required before publication

1. Generate opaque IDs from a release-specific secret salt kept outside the
   public archive.
2. Review every candidate field against the dataset license and privacy route.
3. Populate one archive per headline experiment, including failed attempts and
   exclusions.
4. Recompute the public aggregates from only the minimized archive.
5. Deposit exact immutable bytes in Zenodo, OSF or an equivalent archive and
   commit the DOI, file hashes, code commit and omission rationale.

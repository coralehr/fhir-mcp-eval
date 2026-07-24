# W1A prejoin result

Status: **exploratory support; opaque grading sensitivity pending**.

W1A tested whether the data layer should resolve a visit-to-resource join before
the model answers. Across all 409 questions, prejoin scored 229/409 (56.0%) and
the A6a comparator scored 221/409 (54.0%): +2.0 points, patient-cluster 95% CI
-0.9 to +4.5, McNemar p=.256.

The planned mechanism subset was more promising. On 176 visit-specific
questions, prejoin scored 120/176 (68.2%) versus 108/176 (61.4%): +6.8 points,
patient-cluster 95% CI +1.8 to +12.4, McNemar p=.0075. On 226 patient-scope
questions, no gain was detected: -1.8 points, 95% CI -5.8 to +2.6.

## What this supports

The observed gain concentrated in questions that require resolving a specific
visit. This is evidence for a deterministic join primitive in the context
layer. It is not evidence that a native graph database is necessary, and it
does not show that every non-visit question is unaffected.

## Why this is not yet a licensed confirmatory claim

- The historical judge prompt exposed arm names in its item IDs. Panel grading
  supplied 550 of 818 arm-answer labels; deterministic grading supplied 268.
- The answer manifests retained Codex CLI 0.144.1 but did not pin or record the
  answer model or reasoning effort.
- The local protocol file existed before the answer run, but it was not
  committed before the run and was modified after results existed.
- The W1 summary manifest was overwritten during batched resume and reports
  only the final batch size, even though 409 answer artifacts and usage receipts
  reconcile on disk.

The raw verdict maps remain local because they contain benchmark identifiers.
The aggregate receipt in [W1A_RESULT.json](W1A_RESULT.json) records their hashes,
the exact paired counts, uncertainty, token totals, and provenance limits.

## Economics

W1A used 22,113,585 cumulative input tokens and 130,883 output tokens. The A6a
comparator used 28,776,576 cumulative input tokens and 143,530 output tokens.
These are subscription usage receipts, not provider-priced API cost.

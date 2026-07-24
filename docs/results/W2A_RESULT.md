# W2A agent-join result

Status: **exploratory and unresolved; opaque grading sensitivity pending**.

W2A let the agent perform the same visit-to-resource join through tools. On the
176 visit-specific questions, it scored 127/176 (72.2%) versus prejoin at
120/176 (68.2%): +4.0 points. The paired table contains 30 W2A-only wins and 23
prejoin-only wins, with 97 both correct and 26 both incorrect. McNemar p=.41;
the patient-cluster 95% interval is -8.7 to +17.6 points.

No accuracy difference was detected. The interval is wide, so the run neither
proves equivalence nor proves that agent-side joining is better. It does show
that the two methods fail on different questions, which makes a burned-corpus
hybrid worth developing before a fresh confirmation.

## Economics

W2A used 33,742,927 cumulative input tokens on these 176 questions. Prejoin used
8,308,129 on the same questions. That is 4.06 times as many cumulative input
tokens, not a 4.06-times-larger single context window. Output tokens were
150,330 versus 56,147. These are subscription usage receipts, not API prices.

## Why this is not yet a licensed claim

- The historical judge prompt exposed the W2A arm name in its item IDs. Panel
  grading supplied 130 of 176 labels; deterministic grading supplied 46.
- The answer manifest retained Codex CLI 0.144.1 but did not pin or record the
  answer model or reasoning effort.
- The local protocol file was not committed before the run and was modified
  after results existed.

The aggregate receipt in [W2A_RESULT.json](W2A_RESULT.json) preserves the paired
counts, token totals, source hashes, and limitations without publishing answer
text or benchmark identifiers.

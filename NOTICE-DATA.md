# Data licensing and attribution notice

## What data this repository redistributes

The committed evaluation data — `final_dataset/*.csv` (e.g. `full_test409.csv`,
`full_test200.csv`: question / SQL / gold-answer splits) and derived per-question
artifacts that carry gold answers (e.g. `medplum-eval/full409_answers.json`) — is
**derived from the MIMIC-IV Clinical Database Demo on FHIR** (version 2.1.0), an
open-access, fully de-identified 100-patient demo dataset published on PhysioNet:

- https://physionet.org/content/mimic-iv-fhir-demo/2.1.0/

These files are the FHIR-AgentBench benchmark's question/answer data. The same
question+answer CSVs are published by the benchmark authors in the upstream
repository this fork extends ([glee4810/FHIR-AgentBench](https://github.com/glee4810/FHIR-AgentBench));
this fork's `full_test409.csv` / `full_test200.csv` are re-splits of that
already-public data, not new exposure. No credentialed MIMIC-IV data is included:
patient identifiers and dates are the demo's de-identified, date-shifted values.

## License of the source data (ODbL v1.0)

The MIMIC-IV Clinical Database Demo on FHIR is distributed under the **Open Data
Commons Open Database License (ODbL) v1.0**:

- License text: https://opendatacommons.org/licenses/odbl/1-0/
- PhysioNet license page: https://physionet.org/content/mimic-iv-fhir-demo/view-license/2.1.0/

As required by the ODbL for derived databases, this notice states that the data
files listed above contain information derived from that ODbL-licensed database,
and that database content remains subject to the ODbL v1.0. This repository's
[LICENSE](LICENSE) (CC BY 4.0, matching upstream) covers the fork's **code and
documentation**; it does not re-license the ODbL-derived data.

## Required citations

PhysioNet requires the following citations when using this data (citation block
verified against the PhysioNet page, 2026-07-03):

**Dataset:**

> Bennett, A., Ulrich, H., Wiedekopf, J., Szul, P., Grimes, J., & Johnson, A.
> (2025). MIMIC-IV Clinical Database Demo on FHIR (version 2.1.0). PhysioNet.
> RRID:SCR_007345.

**Original publication:**

> Bennett AM, Ulrich H, van Damme P, Wiedekopf J, Johnson AE. MIMIC-IV on FHIR:
> converting a decade of in-patient data into an exchangeable, interoperable
> format. Journal of the American Medical Informatics Association. 2023 Apr
> 1;30(4):718–25. https://doi.org/10.1093/jamia/ocad002

**PhysioNet:**

> Goldberger, A., Amaral, L., Glass, L., Hausdorff, J., Ivanov, P. C., Mark, R.,
> ... & Stanley, H. E. (2000). PhysioBank, PhysioToolkit, and PhysioNet:
> Components of a new research resource for complex physiologic signals.
> Circulation [Online]. 101(23), pp. e215–e220. RRID:SCR_007345.

**Benchmark (the questions/answers themselves):**

> FHIR-AgentBench — Lee et al., ML4H 2025, arXiv:2509.19319.
> https://github.com/glee4810/FHIR-AgentBench

## Not for clinical use

The demo data is de-identified and intended for education, evaluation, and
software development. Nothing in this repository is real, identifiable patient
data, and nothing here is suitable for clinical decision-making.

# FHIR Retrieval Playbook For The Eval Agent

Use this playbook only as task guidance. It is not an access-control mechanism and it does not override the eval packet or tool outputs.

1. Identify the requested clinical object before retrieving or answering: resource type, code/display, date window, value type, and whether the question asks for earliest, latest, first, last, count, trend, or existence.
2. Preserve temporal intent. If the question asks for first or earliest, do not answer from a recency-only slice unless the packet/tool evidence proves it contains the earliest value. For longitudinal questions, keep first and last values when available.
3. Resolve references deliberately. A MedicationRequest, Encounter, Observation, Condition, Procedure, or DiagnosticReport may point to the clinically relevant resource through references; follow the reference before concluding data is absent.
4. Prefer codes over display text when both are present. Preserve units, coding systems, dates, and comparators exactly as supplied.
5. Avoid repeated identical calls. If live tools are available, narrow by patient, resource type, code/category, date, and count before broad retrieval.
6. Cite source resources as `ResourceType/id`. If the answer depends on a referenced resource, cite both the requesting resource and the referenced resource.
7. Do not guess. If the packet or tools do not contain enough evidence, state the insufficiency and the missing resource/date/code needed.

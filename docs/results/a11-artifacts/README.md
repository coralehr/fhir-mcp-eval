# Preserved A11 aggregate artifacts

These files were copied byte-for-byte from the finalized official Mac mini A11
controller after registered finalization. They contain aggregate labels,
verdicts, manifests, and the final result. They do not contain raw per-question
event logs or private data; the experiment corpus is synthetic and non-PHI.

| File | SHA-256 | Purpose |
|---|---|---|
| [`../A11_RESULT.json`](../A11_RESULT.json) | `95e0dfddbba5aeddd9822f9a9c0d6e3a20c7333fe82b0386d0e26467b6c3b27d` | Registered final result |
| [`result-manifest.json`](result-manifest.json) | `493f7828f41dd7940f9a639313e045fcdf459c168d134e01869265dbd1d71a9b` | Finalizer output binding |
| [`grading-manifest.json`](grading-manifest.json) | `f259cc57e1fc55ae238a8b3aa2cf643c678bd9f39c532a36152ce42f0f19f968` | Completion, grading, economics, and panel configuration binding |
| [`deterministic-labels.json`](deterministic-labels.json) | `dfe8df9d63ffc990946731e750521fc0ebd7a864c4fad520de78435560a51e76` | Deterministic labels, including abstentions and unanswerable cases |
| [`panel-verdicts-manifest.json`](panel-verdicts-manifest.json) | `c4e77a5aea16a0d650c35b2f49b8ef125485baf3fde4f8f1f48e257ab3f7d3d9` | Arm-blind panel replay and token binding |
| [`panel-verdicts.json`](panel-verdicts.json) | `bbebe7a27063d0347ec4f3afaf808719c1e20e1205da19349c31c41d63b8f629` | Three-vote majority labels for 192 substantive answer items |

The official controller manifest SHA-256 is
`3f1209ebc750c7f9eeb67d0a7e5ed3a455aa91dbda2be2ffd4c1905fe192fdce`.
The controller manifest remains sealed read-only with the raw receipts on the
execution host. The raw controller, answer, grading, panel, log, and result
trees were also archived as
`a11-vte-official-0715-3f1209eb.tar.gz` at SHA-256
`1d609dbf96ce28dab2ca59cd9de12e2a79a8cd4fb3caf20c01df6dbb8e477449`;
the archive and all three arm trees are read-only. The committed controller
implementation and preregistration define how the run was produced and
replayed.

#!/usr/bin/env python3
"""Build evidence packets with the current promoted Bonfire recipe.

This is the product-facing entrypoint. Historical A6/QT experiment commands
continue to call ``a6_packet_builder.py`` directly and therefore preserve
their original explicit-feature behavior.
"""

from __future__ import annotations

from a6_packet_builder import PROMOTED_EVIDENCE_RECIPE, main


DEFAULT_EVIDENCE_RECIPE = PROMOTED_EVIDENCE_RECIPE


if __name__ == "__main__":
    raise SystemExit(main(default_evidence_recipe=DEFAULT_EVIDENCE_RECIPE))

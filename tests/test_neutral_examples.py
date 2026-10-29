"""Task 24b: tool/schema examples stay neutral so they fit any business."""

import re

from app import agent


def test_tools_descriptions_contain_no_boutique_salon_only_examples():
    texts = []

    def _walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    texts.append(value)
                else:
                    _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(agent.TOOLS)  # propose_actions embeds PROPOSAL_SCHEMA, so this covers actions.py too
    assert texts  # the walk actually found descriptions
    blob = "\n".join(texts).lower()
    for token in ("fular", "keratin", "saç kesimi", "saç boyama", "boya", "çanta", "elbise",
                  "ayakkabı", "manikür", "pedikür", "makyaj", "kuaför", "berber", "fön"):
        assert re.search(r"(?<![\wçğıöşü])" + re.escape(token) + r"(?![\wçğıöşü])", blob) is None, token

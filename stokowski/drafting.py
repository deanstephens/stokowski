"""Conversational ticket drafting for the Slack create-ticket flow (#19).

A drafting turn is a stateless `claude -p` call (see runner.run_oneshot) over
the conversation so far. The model replies conversationally AND emits the
current draft in a fenced ```ticket block that this module parses.
"""

from __future__ import annotations

import re
from typing import Any

DRAFT_SYSTEM = """You are helping a user turn a rough idea into a high-quality \
engineering ticket that an autonomous coding agent can pick up and implement \
without further clarification.

Your job each turn:
- Reply conversationally: acknowledge, and ask the FEWEST targeted clarifying \
questions needed (scope, constraints, edge cases, acceptance criteria). Do not \
interrogate — infer sensible defaults and state them.
- Then emit the current draft of the ticket in a fenced block exactly like:

```ticket
Title: <concise imperative title>
READY: <yes|no>

## Problem
<what and why, 1-3 sentences>

## Acceptance criteria
- <testable, specific>
- <...>

## Notes / constraints
<optional scope limits, out-of-scope, tech hints>
```

Rules:
- Set `READY: yes` ONLY when the title is clear and the acceptance criteria are \
specific and testable enough to implement from. Otherwise `READY: no`.
- Always include the ```ticket block, even early on (mark it `READY: no`).
- Keep the title short and imperative (e.g. "Add mathutil.Min helper").
- Never invent requirements the user rejected; fold their answers into the draft.
"""

_TICKET_RE = re.compile(r"```ticket\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def build_draft_prompt(conversation: list[dict[str, str]]) -> str:
    """Build the full one-shot prompt from the drafting conversation.

    `conversation` is a list of {role: "user"|"assistant", text: str}.
    """
    parts = [DRAFT_SYSTEM, "", "# Conversation so far", ""]
    for turn in conversation:
        who = "User" if turn.get("role") == "user" else "You"
        parts.append(f"{who}: {(turn.get('text') or '').strip()}")
    parts.append("")
    parts.append(
        "Write your next reply now — conversational text, then the ```ticket block."
    )
    return "\n".join(parts)


def parse_draft(text: str) -> dict[str, Any]:
    """Extract {title, description, ready} from a drafting reply.

    Looks for the fenced ```ticket block. `ready` is only true when the model
    marked READY: yes AND a title and a non-empty body are present.
    """
    empty = {"title": "", "description": "", "ready": False}
    m = _TICKET_RE.search(text or "")
    if not m:
        return empty
    title = ""
    ready_flag = False
    body_lines: list[str] = []
    for line in m.group(1).splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("title:") and not title:
            title = stripped.split(":", 1)[1].strip()
        elif low.startswith("ready:"):
            ready_flag = "yes" in low.split(":", 1)[1]
        else:
            body_lines.append(line)
    description = "\n".join(body_lines).strip()
    return {
        "title": title,
        "description": description,
        "ready": bool(ready_flag and title and description),
    }

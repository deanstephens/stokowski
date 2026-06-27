"""Two-way Slack integration.

Outbound: pushes human-review gates (with Approve / Request rework / View
buttons), error/escalation alerts, and run-completion notices to a Slack
channel via the Web API (`chat.postMessage`).

Inbound (handled in web.py): button clicks and thread replies are verified
with the Slack signing secret and translated into Linear gate-state changes,
reusing the existing gate state machine rather than a parallel control path.

Only depends on httpx (already a core dependency) and the stdlib — no slack_sdk.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from .models import Issue

logger = logging.getLogger("stokowski.slack")

SLACK_API = "https://slack.com/api"

# action_id values for the interactive buttons.
ACTION_APPROVE = "stokowski_approve"
ACTION_REWORK = "stokowski_rework"


def encode_action_value(issue_id: str, gate: str, run: int) -> str:
    """Encode the issue context carried by an interactive button."""
    return json.dumps({"issue": issue_id, "gate": gate, "run": run})


def decode_action_value(value: str) -> dict[str, Any]:
    """Decode a button value back into its issue context. Returns {} on error."""
    try:
        data = json.loads(value)
        if isinstance(data, dict):
            return data
    except (ValueError, TypeError):
        pass
    return {}


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    now: float | None = None,
    max_skew_s: int = 60 * 5,
) -> bool:
    """Verify an inbound Slack request signature (v0 scheme).

    See https://api.slack.com/authentication/verifying-requests-from-slack.
    Rejects requests whose timestamp is older than ``max_skew_s`` (replay
    protection). ``now`` is injectable for testing.
    """
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False
    current = time.time() if now is None else now
    if abs(current - ts) > max_skew_s:
        return False
    basestring = b"v0:" + timestamp.encode() + b":" + body
    digest = hmac.new(
        signing_secret.encode(), basestring, hashlib.sha256
    ).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)


def build_gate_blocks(
    issue: Issue,
    gate_state: str,
    prompt: str,
    run: int,
    question: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Build the (fallback_text, blocks) for a human-review gate message."""
    title = issue.title or issue.identifier
    fallback = f"[Stokowski] {issue.identifier} awaiting review at {gate_state}: {title}"

    header_lines = [
        f":hourglass_flowing_sand: *Awaiting human review* — `{gate_state}`",
        f"*<{issue.url or ''}|{issue.identifier}>* {title}",
    ]
    if run > 1:
        header_lines.append(f"_run {run}_")
    section_text = "\n".join(header_lines)

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": section_text}},
    ]
    if prompt:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f">{prompt}"}}
        )
    if question:
        q = question.strip()
        if len(q) > 2800:
            q = q[:2800] + "…"
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":speech_balloon: *Agent:*\n{q}"},
            }
        )

    value = encode_action_value(issue.id, gate_state, run)
    elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Approve"},
            "style": "primary",
            "action_id": ACTION_APPROVE,
            "value": value,
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Request rework"},
            "style": "danger",
            "action_id": ACTION_REWORK,
            "value": value,
        },
    ]
    if issue.url:
        elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "View issue"},
                "url": issue.url,
                "action_id": "stokowski_view",
            }
        )
    blocks.append({"type": "actions", "elements": elements})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "Reply in this thread to send feedback to the agent."}
            ],
        }
    )
    return fallback, blocks


class SlackNotifier:
    """Posts notifications to Slack and tracks gate message threads.

    Thread bookkeeping lets follow-up error/done notices and inbound thread
    replies attach to the originating gate message. Mappings live in memory;
    on restart, threading simply resets (notifications still post).
    """

    def __init__(self, bot_token: str, channel: str, signing_secret: str = ""):
        self.bot_token = bot_token
        self.channel = channel
        self.signing_secret = signing_secret
        self._client: httpx.AsyncClient | None = None
        # issue_id -> thread ts of its gate message
        self._issue_thread: dict[str, str] = {}
        # thread ts -> issue_id (reverse lookup for inbound replies)
        self._thread_issue: dict[str, str] = {}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post_message(
        self,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        thread_ts: str | None = None,
    ) -> str | None:
        """Call chat.postMessage. Returns the message ts on success, else None."""
        payload: dict[str, Any] = {"channel": self.channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts
        try:
            resp = await self._http().post(
                f"{SLACK_API}/chat.postMessage",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json=payload,
            )
            data = resp.json()
        except Exception as exc:  # network / json errors must never crash the daemon
            logger.warning(f"Slack post failed: {exc}")
            return None
        if not data.get("ok"):
            logger.warning(f"Slack post rejected: {data.get('error')}")
            return None
        return data.get("ts")

    # --- outbound notifications -------------------------------------------

    async def notify_gate(
        self,
        issue: Issue,
        gate_state: str,
        prompt: str = "",
        run: int = 1,
        question: str | None = None,
    ) -> None:
        text, blocks = build_gate_blocks(issue, gate_state, prompt, run, question)
        ts = await self._post_message(text, blocks)
        if ts:
            self._issue_thread[issue.id] = ts
            self._thread_issue[ts] = issue.id

    async def notify_error(self, issue: Issue, kind: str, detail: str = "") -> None:
        title = issue.title or issue.identifier
        text = f":rotating_light: [Stokowski] {issue.identifier} {kind}"
        body = f":rotating_light: *{kind}* — *<{issue.url or ''}|{issue.identifier}>* {title}"
        if detail:
            clipped = detail if len(detail) < 1000 else detail[:1000] + "…"
            body += f"\n```{clipped}```"
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": body}}]
        await self._post_message(text, blocks, thread_ts=self._issue_thread.get(issue.id))

    async def notify_done(self, issue: Issue, summary: str = "") -> None:
        title = issue.title or issue.identifier
        text = f":white_check_mark: [Stokowski] {issue.identifier} done"
        body = f":white_check_mark: *Completed* — *<{issue.url or ''}|{issue.identifier}>* {title}"
        if summary:
            clipped = summary if len(summary) < 1500 else summary[:1500] + "…"
            body += f"\n{clipped}"
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": body}}]
        await self._post_message(text, blocks, thread_ts=self._issue_thread.get(issue.id))

    async def acknowledge(self, issue_id: str, text: str) -> None:
        """Post a short threaded acknowledgement under an issue's gate message."""
        await self._post_message(text, thread_ts=self._issue_thread.get(issue_id))

    async def post_agent_reply(self, issue_id: str, text: str) -> None:
        """Post the agent's conversational reply into the issue's gate thread."""
        thread_ts = self._issue_thread.get(issue_id)
        if not thread_ts:
            return
        body = text.strip()
        if len(body) > 3500:
            body = body[:3500] + "…"
        await self._post_message(f":robot_face: {body}", thread_ts=thread_ts)

    def has_thread(self, issue_id: str) -> bool:
        """True if a gate thread is being tracked for this issue."""
        return issue_id in self._issue_thread

    # --- inbound lookup ----------------------------------------------------

    def issue_for_thread(self, thread_ts: str) -> str | None:
        """Resolve the issue id a Slack thread belongs to (for reply handling)."""
        return self._thread_issue.get(thread_ts)

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
from typing import Any, Awaitable, Callable

import httpx

from .models import Issue

logger = logging.getLogger("stokowski.slack")

SLACK_API = "https://slack.com/api"

# action_id values for the interactive buttons.
ACTION_APPROVE = "stokowski_approve"
ACTION_REWORK = "stokowski_rework"
ACTION_CREATE_TICKET = "stokowski_create_ticket"
ACTION_CANCEL_TICKET = "stokowski_cancel_ticket"


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

    def __init__(
        self,
        bot_token: str,
        channel: str,
        signing_secret: str = "",
        *,
        mentions: bool = False,
        user_map: dict[str, str] | None = None,
    ):
        self.bot_token = bot_token
        self.channel = channel
        self.signing_secret = signing_secret
        self.mentions = mentions
        # email (lowercased) -> Slack user id, manual override from config.
        self.user_map = {k.lower(): v for k, v in (user_map or {}).items()}
        self._client: httpx.AsyncClient | None = None
        # issue_id -> thread ts of its gate message
        self._issue_thread: dict[str, str] = {}
        # thread ts -> issue_id (reverse lookup for inbound replies)
        self._thread_issue: dict[str, str] = {}
        # thread ts -> set of Slack user ids who replied in that thread
        self._thread_participants: dict[str, set[str]] = {}
        # issue_id -> creator email (so follow-ups can ping the creator too)
        self._issue_creator: dict[str, str] = {}
        # issue_ids whose current gate-round decision was already announced
        # (dedupes the Slack-button and Linear-state-change paths).
        self._announced_decisions: set[str] = set()
        # email (lowercased) -> Slack user id | None, lookupByEmail cache
        self._email_uid_cache: dict[str, str | None] = {}
        # this bot's own Slack user id (for @-mention detection), cached.
        self.bot_user_id: str | None = None

    async def bot_id(self) -> str | None:
        """Resolve (and cache) this bot's own Slack user id via auth.test."""
        if self.bot_user_id is None:
            try:
                resp = await self._http().get(
                    f"{SLACK_API}/auth.test",
                    headers={"Authorization": f"Bearer {self.bot_token}"},
                )
                data = resp.json()
                if data.get("ok"):
                    self.bot_user_id = data.get("user_id")
            except Exception as exc:
                logger.warning(f"Slack auth.test failed: {exc}")
        return self.bot_user_id

    # --- ticket-drafting posts --------------------------------------------

    async def post_new_draft(self, text: str) -> str | None:
        """Post a new top-level message that becomes a ticket-draft thread root."""
        return await self._post_message(text)

    async def post_thread_text(self, thread_ts: str, text: str) -> None:
        """Post a plain message into a thread."""
        await self._post_message(text, thread_ts=thread_ts)

    async def post_ticket_buttons(self, thread_ts: str) -> None:
        """Post Create ticket / Cancel buttons under a ready draft."""
        blocks = [
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Create ticket"},
                        "style": "primary",
                        "action_id": ACTION_CREATE_TICKET,
                        "value": thread_ts,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Cancel"},
                        "action_id": ACTION_CANCEL_TICKET,
                        "value": thread_ts,
                    },
                ],
            }
        ]
        await self._post_message(
            "This draft looks ready — create it?", blocks, thread_ts=thread_ts
        )

    # --- targeted-mention helpers -----------------------------------------

    async def _uid_for_email(self, email: str | None) -> str | None:
        """Resolve a Linear/email identity to a Slack user id (cached)."""
        if not email:
            return None
        key = email.lower()
        if key in self.user_map:
            return self.user_map[key]
        if key in self._email_uid_cache:
            return self._email_uid_cache[key]
        uid: str | None = None
        try:
            resp = await self._http().get(
                f"{SLACK_API}/users.lookupByEmail",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                params={"email": email},
            )
            data = resp.json()
            if data.get("ok"):
                uid = (data.get("user") or {}).get("id")
            else:
                logger.debug(f"users.lookupByEmail: {data.get('error')}")
        except Exception as exc:
            logger.warning(f"Slack user lookup failed: {exc}")
        self._email_uid_cache[key] = uid
        return uid

    def record_participant(self, thread_ts: str, slack_uid: str) -> None:
        """Remember a human who replied in a thread (for follow-up pings)."""
        if thread_ts and slack_uid:
            self._thread_participants.setdefault(thread_ts, set()).add(slack_uid)

    async def mentions_for(self, issue_id: str, exclude: set[str] | None = None) -> str:
        """Build a de-duped `<@U…> ` mention prefix for an issue's followers.

        Includes the issue creator (resolved via email) and everyone who has
        replied in its gate thread. Returns "" when mentions are disabled or
        nobody resolves.
        """
        if not self.mentions:
            return ""
        seen: set[str] = set(exclude or [])
        ordered: list[str] = []
        creator_uid = await self._uid_for_email(self._issue_creator.get(issue_id))
        thread_ts = self._issue_thread.get(issue_id, "")
        candidates = ([creator_uid] if creator_uid else []) + sorted(
            self._thread_participants.get(thread_ts, set())
        )
        for uid in candidates:
            if uid and uid not in seen:
                seen.add(uid)
                ordered.append(uid)
        return "".join(f"<@{u}> " for u in ordered)

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

    async def _permalink(self, ts: str) -> str | None:
        """Resolve a public permalink to a posted message (chat.getPermalink)."""
        try:
            resp = await self._http().get(
                f"{SLACK_API}/chat.getPermalink",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                params={"channel": self.channel, "message_ts": ts},
            )
            data = resp.json()
            if data.get("ok"):
                return data.get("permalink")
            logger.debug(f"chat.getPermalink: {data.get('error')}")
        except Exception as exc:
            logger.warning(f"Slack permalink lookup failed: {exc}")
        return None

    async def notify_gate(
        self,
        issue: Issue,
        gate_state: str,
        prompt: str = "",
        run: int = 1,
        question: str | None = None,
        on_permalink: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        # Remember the creator so this and later follow-ups can ping them.
        if issue.creator_email:
            self._issue_creator[issue.id] = issue.creator_email
        # New gate round → allow the next decision to be announced.
        self._announced_decisions.discard(issue.id)

        text, blocks = build_gate_blocks(issue, gate_state, prompt, run, question)
        # Ping the creator (and, on a re-review, prior thread participants).
        mention = await self.mentions_for(issue.id)

        existing = self._issue_thread.get(issue.id)
        if run > 1 and existing:
            # Re-review after rework: continue in the SAME thread so the
            # reviewer gets the update where they sent it back, instead of
            # orphaning it in a brand-new top-level message/thread.
            header = f":repeat: {mention}*Back for review after rework* (run {run})"
            blocks = [
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": header}},
            ] + blocks
            text = f"{header.replace('*', '')}\n{text}"
            await self._post_message(text, blocks, thread_ts=existing)
            return  # keep the existing thread mapping

        # First review: a new top-level message starts the thread.
        if mention:
            ping = f":eyes: {mention}— your issue is ready for review"
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": ping}}
            ] + blocks
            text = f"{ping}\n{text}"
        ts = await self._post_message(text, blocks)
        if ts:
            self._issue_thread[issue.id] = ts
            self._thread_issue[ts] = issue.id
            # Hand back a permalink so the caller can link the Linear card to
            # this thread (the gate message already links the other way).
            if on_permalink:
                link = await self._permalink(ts)
                if link:
                    await on_permalink(link)

    async def notify_error(self, issue: Issue, kind: str, detail: str = "") -> None:
        title = issue.title or issue.identifier
        if issue.creator_email:
            self._issue_creator.setdefault(issue.id, issue.creator_email)
        text = f":rotating_light: [Stokowski] {issue.identifier} {kind}"
        mention = await self.mentions_for(issue.id)
        body = f"{mention}:rotating_light: *{kind}* — *<{issue.url or ''}|{issue.identifier}>* {title}"
        if detail:
            clipped = detail if len(detail) < 1000 else detail[:1000] + "…"
            body += f"\n```{clipped}```"
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": body}}]
        await self._post_message(text, blocks, thread_ts=self._issue_thread.get(issue.id))

    async def notify_done(self, issue: Issue, summary: str = "") -> None:
        title = issue.title or issue.identifier
        if issue.creator_email:
            self._issue_creator.setdefault(issue.id, issue.creator_email)
        text = f":white_check_mark: [Stokowski] {issue.identifier} done"
        mention = await self.mentions_for(issue.id)
        body = f"{mention}:white_check_mark: *Completed* — *<{issue.url or ''}|{issue.identifier}>* {title}"
        if summary:
            clipped = summary if len(summary) < 1500 else summary[:1500] + "…"
            body += f"\n{clipped}"
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": body}}]
        await self._post_message(text, blocks, thread_ts=self._issue_thread.get(issue.id))

    async def acknowledge(self, issue_id: str, text: str) -> None:
        """Post a short threaded acknowledgement under an issue's gate message."""
        await self._post_message(text, thread_ts=self._issue_thread.get(issue_id))

    def record_actor(self, issue_id: str, slack_uid: str) -> None:
        """Record someone who acted on an issue's gate (e.g. clicked a button)."""
        self.record_participant(self._issue_thread.get(issue_id, ""), slack_uid)

    async def post_gate_decision(
        self,
        issue_id: str,
        decision: str,
        *,
        actor_uid: str | None = None,
        actor_name: str | None = None,
        source: str | None = None,
        ok: bool = True,
    ) -> None:
        """Post a clearly-separated, attributed gate decision into the thread.

        A `divider` block separates the round that just ended from the action
        and whatever comes next, and the action names who took it (@-mentioning
        them when their Slack id is known, else a neutral ``source`` like
        "in Linear").

        Idempotent per gate round: the Slack-button path and the orchestrator's
        Linear-state-change path may both call this for the same decision, but
        only the first announces it. The marker is reset when the gate is
        (re-)entered (see :meth:`notify_gate`).
        """
        thread_ts = self._issue_thread.get(issue_id)
        if not thread_ts:
            return
        if issue_id in self._announced_decisions:
            return
        self._announced_decisions.add(issue_id)
        if not ok:
            headline = ":warning: *Could not apply the decision*"
        elif decision == "approve":
            headline = ":white_check_mark: *Approved*"
        else:
            headline = ":leftwards_arrow_with_hook: *Sent back for rework*"
        if actor_uid:
            headline += f" — by <@{actor_uid}>"
        elif actor_name:
            headline += f" — by {actor_name}"
        elif source:
            headline += f" — {source}"
        blocks = [
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": headline}},
        ]
        await self._post_message(headline.replace("*", ""), blocks, thread_ts=thread_ts)

    async def post_agent_reply(self, issue_id: str, text: str) -> None:
        """Post the agent's conversational reply into the issue's gate thread."""
        thread_ts = self._issue_thread.get(issue_id)
        if not thread_ts:
            return
        body = text.strip()
        if len(body) > 3500:
            body = body[:3500] + "…"
        mention = await self.mentions_for(issue_id)
        await self._post_message(f"{mention}:robot_face: {body}", thread_ts=thread_ts)

    def has_thread(self, issue_id: str) -> bool:
        """True if a gate thread is being tracked for this issue."""
        return issue_id in self._issue_thread

    # --- inbound lookup ----------------------------------------------------

    def issue_for_thread(self, thread_ts: str) -> str | None:
        """Resolve the issue id a Slack thread belongs to (for reply handling)."""
        return self._thread_issue.get(thread_ts)

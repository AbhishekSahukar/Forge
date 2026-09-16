"""
Phase 4/7 — Approval store and deterministic execution of approved fixes.

BUG FIX (Phase 7): this used to be a plain in-memory dict. That's fine
for a single process, but run_all.py runs FOUR separate OS processes
(knowledge-agent, engineering-agent, review-agent, main-api) — a
Python module-level variable does NOT share across processes. The
Engineering Agent (port 9002) was storing proposals in its own
process's memory, while main-api (port 8000), which serves
GET/POST /agent/proposals/*, had a completely separate, empty copy of
the same dict. Proposals were created successfully but permanently
invisible to every endpoint. This is now backed by a SQLite file all
four processes open, so whichever process writes a proposal, every
other process can read it immediately.

This is still the ONE place in Forge that calls write tools on the
real GitHub MCP server. It does not involve an LLM at all: by the time
something reaches approve_and_execute(), a human has already approved
an exact, fully-specified plan (branch name, file contents, commit
messages, PR title/body) produced by the Engineering Agent. Execution
here is just "do exactly what the approved plan says," which is the
whole point of separating propose (Safe Mode) from execute (Autonomous
Mode, but only for already-approved work).

IMPORTANT — verify before running for real:
The exact argument names below (owner/repo/branch/path/content/message/
sha/base/head/title/body) were confirmed against a live server's tool
schema via test_inspect_write_tools.py, and approve_and_execute has
been run successfully in practice. If GitHub's MCP server changes its
schema in the future, re-run that script to confirm.
"""

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

from app.agents.engineering_agent import ProposedChange, ProposedFile
from app.mcp.client import MCPToolkit

# backend/forge_data.db — a fixed path derived from this file's own
# location, not the process's current working directory, so all four
# run_all.py processes resolve to the exact same file regardless of
# where each was launched from.
DB_PATH = Path(__file__).resolve().parent.parent.parent / "forge_data.db"


class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class Proposal:
    """
    In-memory representation used by callers (the FastAPI router,
    the A2A executors). Constructed fresh from a DB row on every read
    — this class itself holds no state between calls.
    """

    def __init__(self, id: str, change: ProposedChange, owner: str, repo: str, status: ProposalStatus):
        self.id = id
        self.change = change
        self.owner = owner
        self.repo = repo
        self.status = status
        self.result: dict | None = None
        self.error: str | None = None
        self.review_verdict: str | None = None
        self.review_reasoning: str | None = None
        self.review_concerns: list[str] = []
        self.review_raw: str | None = None


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")  # lets multiple processes read/write safely
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                repo TEXT NOT NULL,
                status TEXT NOT NULL,
                branch_name TEXT NOT NULL,
                base_branch TEXT NOT NULL,
                pr_title TEXT NOT NULL,
                pr_body TEXT NOT NULL,
                raw_task TEXT NOT NULL,
                files_json TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                review_verdict TEXT,
                review_reasoning TEXT,
                review_concerns_json TEXT,
                review_raw TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


_init_db()


def _row_to_proposal(row: sqlite3.Row) -> Proposal:
    files = [ProposedFile(**f) for f in json.loads(row["files_json"])]
    change = ProposedChange(
        branch_name=row["branch_name"],
        base_branch=row["base_branch"],
        files=files,
        pr_title=row["pr_title"],
        pr_body=row["pr_body"],
        raw_task=row["raw_task"],
    )
    proposal = Proposal(
        id=row["id"],
        change=change,
        owner=row["owner"],
        repo=row["repo"],
        status=ProposalStatus(row["status"]),
    )
    proposal.result = json.loads(row["result_json"]) if row["result_json"] else None
    proposal.error = row["error"]
    proposal.review_verdict = row["review_verdict"]
    proposal.review_reasoning = row["review_reasoning"]
    proposal.review_concerns = json.loads(row["review_concerns_json"]) if row["review_concerns_json"] else []
    proposal.review_raw = row["review_raw"]
    return proposal


def store_proposal(change: ProposedChange, owner: str, repo: str) -> Proposal:
    proposal_id = str(uuid.uuid4())
    files_json = json.dumps([f.model_dump() for f in change.files])
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO proposals
                (id, owner, repo, status, branch_name, base_branch, pr_title, pr_body, raw_task, files_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal_id,
                owner,
                repo,
                ProposalStatus.PENDING.value,
                change.branch_name,
                change.base_branch,
                change.pr_title,
                change.pr_body,
                change.raw_task,
                files_json,
            ),
        )
    return get_proposal(proposal_id)


def get_proposal(proposal_id: str) -> Proposal | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return _row_to_proposal(row) if row else None


def list_proposals() -> list[Proposal]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM proposals ORDER BY created_at DESC").fetchall()
        return [_row_to_proposal(r) for r in rows]


def set_review(
    proposal_id: str, verdict: str | None, reasoning: str | None, concerns: list[str], raw: str | None
) -> None:
    """Called by the Engineering Agent server right after it gets the Review Agent's verdict."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE proposals
            SET review_verdict = ?, review_reasoning = ?, review_concerns_json = ?, review_raw = ?
            WHERE id = ?
            """,
            (verdict, reasoning, json.dumps(concerns), raw, proposal_id),
        )


def reject_proposal(proposal_id: str) -> Proposal:
    with _connect() as conn:
        conn.execute(
            "UPDATE proposals SET status = ? WHERE id = ?", (ProposalStatus.REJECTED.value, proposal_id)
        )
    return get_proposal(proposal_id)


def _set_status(proposal_id: str, status: ProposalStatus, *, result: dict | None = None, error: str | None = None):
    with _connect() as conn:
        conn.execute(
            "UPDATE proposals SET status = ?, result_json = ?, error = ? WHERE id = ?",
            (status.value, json.dumps(result) if result is not None else None, error, proposal_id),
        )


async def approve_and_execute(proposal_id: str) -> Proposal:
    """
    The only function in Forge that writes to GitHub. Runs the approved
    plan step by step: create branch -> write each file -> open PR.
    """
    proposal = get_proposal(proposal_id)
    if proposal is None:
        raise ValueError(f"Proposal {proposal_id} not found")
    if proposal.status not in (ProposalStatus.PENDING, ProposalStatus.FAILED):
        raise ValueError(
            f"Proposal {proposal_id} cannot be approved from status={proposal.status} "
            "(only pending or previously-failed proposals can be approved/retried)"
        )

    _set_status(proposal_id, ProposalStatus.APPROVED)
    change = proposal.change

    # Guarantee a unique branch name for THIS approval, regardless of
    # what the LLM proposed. Real bug hit in testing: two separate
    # proposals for the same issue both generated "fix/x" as the
    # branch name; the second approval reused the first attempt's
    # already-existing branch, so create_or_update_file had nothing
    # new to commit and create_pull_request correctly refused with
    # "No commits between main and fix/x". Suffixing with part of this
    # proposal's own id makes every approval target a fresh branch,
    # independent of whatever name the LLM happened to pick.
    unique_branch = f"{change.branch_name}-{proposal_id[:8]}"

    # Captures the raw text of every single tool call in order, so if
    # something goes wrong we can see exactly what each step actually
    # returned — not just whichever step happened to raise last. This
    # is what's missing right now: create_pull_request's "no commits"
    # error is a downstream SYMPTOM, and without seeing create_branch's
    # and create_or_update_file's actual raw responses there's no way
    # to tell which of them silently didn't do what it claimed.
    steps_log: list[str] = []

    def log_step(name: str, result: str):
        steps_log.append(f"[{name}] {result[:300]}")

    try:
        async with MCPToolkit() as toolkit:
            # 1. Create the branch off base_branch. On a RETRY (this
            # proposal previously failed after already committing the
            # fix — see the loosened status check above), this branch
            # already exists with that commit on it. That's fine and
            # expected here, not a failure: only raise for a genuine
            # error, not "already exists".
            branch_result = await toolkit._call_tool(
                "create_branch",
                {
                    "owner": proposal.owner,
                    "repo": proposal.repo,
                    "branch": unique_branch,
                    "from_branch": change.base_branch,
                },
            )
            log_step("create_branch", branch_result)
            if "already exists" not in branch_result.lower():
                _raise_if_tool_failed("create_branch", branch_result)

            # 2. Write each file. If the file already exists, GitHub
            # requires the current blob SHA to update it — fetch it
            # first and fall back to "create" semantics if it's new.
            for f in change.files:
                sha = None
                existing = await toolkit._call_tool(
                    "get_file_contents",
                    {
                        "owner": proposal.owner,
                        "repo": proposal.repo,
                        "path": f.path,
                        "ref": f"refs/heads/{unique_branch}",
                    },
                )
                log_step(f"get_file_contents({f.path})", existing)
                sha = _extract_sha(existing)

                args = {
                    "owner": proposal.owner,
                    "repo": proposal.repo,
                    "path": f.path,
                    "content": f.content,
                    "message": f.commit_message,
                    "branch": unique_branch,
                }
                if sha:
                    args["sha"] = sha
                file_result = await toolkit._call_tool("create_or_update_file", args)
                log_step(f"create_or_update_file({f.path})", file_result)
                _raise_if_tool_failed(f"create_or_update_file({f.path})", file_result)

            # 3. Open the pull request
            pr_result = await toolkit._call_tool(
                "create_pull_request",
                {
                    "owner": proposal.owner,
                    "repo": proposal.repo,
                    "title": change.pr_title,
                    "body": change.pr_body,
                    "head": unique_branch,
                    "base": change.base_branch,
                },
            )
            log_step("create_pull_request", pr_result)
            _raise_if_tool_failed("create_pull_request", pr_result)

        _set_status(proposal_id, ProposalStatus.EXECUTED, result={"pull_request": pr_result, "branch": unique_branch})
        return get_proposal(proposal_id)

    except Exception as exc:
        # Attach the full step-by-step log to the error so the UI shows
        # exactly what every prior step actually returned, not just
        # whichever one raised — this is the key diagnostic info that
        # was previously invisible.
        #
        # BUG FIXED: this used to just `raise` (re-raising the
        # original short exception) after storing full_error in the
        # database. The database copy was correct, but the HTTP
        # response — and therefore what the frontend actually displays
        # immediately — only ever carried the short message, since
        # that's what propagated up through the router's
        # `except Exception as exc: raise HTTPException(..., detail=str(exc))`.
        # Raising a NEW exception built from full_error here means the
        # detailed log reaches the response immediately, not just the
        # database (which the UI doesn't re-fetch after a failure).
        full_error = str(exc) + "\n\n--- Full step log ---\n" + "\n".join(steps_log)
        _set_status(proposal_id, ProposalStatus.FAILED, error=full_error)
        raise RuntimeError(full_error) from exc
        # Note: create_branch/create_or_update_file may have already
        # succeeded by the time a later step fails (e.g. PR creation
        # rejected for a permissions reason) — the branch and commit
        # are real and stay on GitHub even though the proposal is
        # marked FAILED here. The error message says which step failed
        # so it's clear what still needs finishing manually or via a
        # retry once the underlying issue (e.g. a PAT scope) is fixed.
        _set_status(proposal_id, ProposalStatus.FAILED, error=str(exc))
        raise


def _raise_if_tool_failed(step_name: str, result_text: str) -> None:
    """
    The GitHub MCP server sometimes returns a failure as plain TEXT
    content (e.g. "failed to create pull request: POST ... 403 ...")
    rather than as an MCP-protocol-level error — _call_tool() has no
    way to distinguish that from a normal successful text result, so
    it doesn't raise on its own. This does the check explicitly after
    every write step, so a failure actually marks the proposal FAILED
    instead of being silently recorded as a successful EXECUTED result
    (which is what happened before this check existed: a PR creation
    that failed with 403 was reported as if it had succeeded).
    """
    lowered = result_text.strip().lower()
    if lowered.startswith("failed to") or lowered.startswith("error:") or '"error"' in lowered[:50]:
        raise RuntimeError(f"{step_name} failed: {result_text}")


def _extract_sha(get_file_contents_result: str) -> str | None:
    """
    Pulls the blob SHA out of get_file_contents()'s response so it can
    be passed to create_or_update_file() when overwriting an existing
    file (GitHub requires this — without it, the write is rejected).

    BUG FIXED: this used to only check for a JSON `"sha"` key
    (`if '"sha"' in existing: json.loads(existing)["sha"]`), on the
    assumption the tool always returns structured JSON. In practice,
    for a single text file, this GitHub MCP server returns a plain
    English sentence instead: "successfully downloaded text file
    (SHA: 679fc99e...)" — no JSON at all. The old check silently never
    matched, sha stayed None, and create_or_update_file always got
    called without one — which is why every single approval attempt
    failed identically with "File already exists... you must provide
    the current file's SHA", no matter how many other things got fixed
    around it (unique branch names, permission scopes, etc. were all
    real fixes, but none of them could matter until this one landed).

    Handles both response shapes: proper JSON with a "sha" key (in
    case other response types use it), and the "(SHA: <hex>)" plain
    text pattern actually observed for single-file reads.
    """
    text = get_file_contents_result.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "sha" in parsed:
            return parsed["sha"]
    except (ValueError, TypeError):
        pass

    match = re.search(r"SHA:\s*([a-fA-F0-9]+)", text)
    return match.group(1) if match else None
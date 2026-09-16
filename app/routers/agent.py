import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.knowledge_agent import investigate
from app.agents.triage_agent import handle_request, handle_request_streaming
from app.core.approval_store import (
    approve_and_execute,
    get_proposal,
    list_proposals,
    reject_proposal,
)
from app.core.deps import get_current_user, require_role

router = APIRouter(prefix="/agent", tags=["agent"])


class InvestigateRequest(BaseModel):
    task: str


class InvestigateResponse(BaseModel):
    report: str


class TriageRequest(BaseModel):
    request: str


class TriageResponse(BaseModel):
    triage_decision: str
    report: str


class ProposalSummary(BaseModel):
    id: str
    status: str
    owner: str
    repo: str
    branch_name: str
    pr_title: str
    files: list[str]


class ProposalDetail(ProposalSummary):
    pr_body: str
    result: dict | None = None
    error: str | None = None
    review_verdict: str | None = None
    review_reasoning: str | None = None
    review_concerns: list[str] = []


def _to_summary(proposal) -> ProposalSummary:
    return ProposalSummary(
        id=proposal.id,
        status=proposal.status.value,
        owner=proposal.owner,
        repo=proposal.repo,
        branch_name=proposal.change.branch_name,
        pr_title=proposal.change.pr_title,
        files=[f.path for f in proposal.change.files],
    )


def _to_detail(proposal) -> ProposalDetail:
    return ProposalDetail(
        **_to_summary(proposal).model_dump(),
        pr_body=proposal.change.pr_body,
        result=proposal.result,
        error=proposal.error,
        review_verdict=proposal.review_verdict,
        review_reasoning=proposal.review_reasoning,
        review_concerns=proposal.review_concerns,
    )


@router.post("/investigate", response_model=InvestigateResponse)
async def investigate_endpoint(
    payload: InvestigateRequest, user=Depends(get_current_user)
) -> InvestigateResponse:
    """
    Phase 1/2: calls the Knowledge Agent directly as a Python function.
    Read-only, so any authenticated role (Viewer included) can call it.
    """
    report = await investigate(payload.task)
    return InvestigateResponse(report=report)


@router.post(
    "/triage",
    response_model=TriageResponse,
    dependencies=[Depends(require_role("developer", "reviewer", "admin"))],
)
async def triage_endpoint(payload: TriageRequest) -> TriageResponse:
    """
    Phase 3/4/6: the real entry point. Routes to Knowledge (investigate)
    or Engineering (propose a fix) over A2A. Restricted above Viewer
    since it can trigger fix-proposal generation, not just reads —
    pure investigation without that risk is still available via
    /agent/investigate for Viewer-level access.
    """
    result = await handle_request(payload.request)
    return TriageResponse(**result)


@router.post(
    "/triage/stream",
    dependencies=[Depends(require_role("developer", "reviewer", "admin"))],
)
async def triage_stream_endpoint(payload: TriageRequest) -> StreamingResponse:
    """
    Phase 7: live AG-UI — same routing as /triage, but streams every
    tool call, tool result, and status change as Server-Sent Events
    instead of waiting for one final response.

    Auth still applies (the require_role dependency above runs before
    streaming starts, same as any other endpoint) — this uses fetch()
    + ReadableStream on the frontend rather than native EventSource,
    specifically because EventSource cannot send an Authorization
    header, and this endpoint needs one.

    Each SSE frame is `data: <json>\\n\\n` where the JSON is one of:
      {"kind": "status", "state": "routing"|"working"|"failed", "text": "..."}
      {"kind": "final", "text": "<the completed report>"}
    """

    async def event_stream():
        try:
            async for event in handle_request_streaming(payload.request):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'kind': 'status', 'state': 'failed', 'text': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disables response buffering on nginx-style proxies sitting
            # in front of this in a real deployment; harmless locally.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/proposals", response_model=list[ProposalSummary])
async def list_proposals_endpoint(user=Depends(get_current_user)) -> list[ProposalSummary]:
    """All proposals this process has seen (in-memory — clears on restart). Read-only."""
    return [_to_summary(p) for p in list_proposals()]


@router.get("/proposals/{proposal_id}", response_model=ProposalDetail)
async def get_proposal_endpoint(
    proposal_id: str, user=Depends(get_current_user)
) -> ProposalDetail:
    proposal = get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return _to_detail(proposal)


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=ProposalDetail,
    dependencies=[Depends(require_role("developer", "admin"))],
)
async def approve_proposal_endpoint(proposal_id: str) -> ProposalDetail:
    """
    Human-in-the-loop gate. This is the ONLY endpoint in Forge that
    results in a real write to GitHub — everything up to this point
    (investigation, proposal generation) is read-only. Restricted to
    Developer/Admin per the roles table (Reviewer can approve/reject a
    *review*, but committing code + opening a PR is Developer territory).
    """
    proposal = get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    try:
        proposal = await approve_and_execute(proposal_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return _to_detail(proposal)


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=ProposalDetail,
    dependencies=[Depends(require_role("developer", "reviewer", "admin"))],
)
async def reject_proposal_endpoint(proposal_id: str) -> ProposalDetail:
    proposal = get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    proposal = reject_proposal(proposal_id)
    return _to_detail(proposal)
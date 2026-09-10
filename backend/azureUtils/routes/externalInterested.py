"""Read-only aggregate contacts report, gated by verified Administrator access."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from azureUtils.storage import interestedCandidates

def create_router(require_admin_token):
    router = APIRouter()

    @router.get("/external/interested")
    def interested_candidates(
        request: Request, domain: str = Query(...), limit: int = Query(50, ge=1, le=100),
        after: int = Query(0, ge=0), jd_id: str = "",
    ):
        # Authorize on the server, before contact queries. A localStorage role or
        # a workspace parameter is not authentication. Keep tokens out of URLs.
        require_admin_token(request.headers.get("X-DevReady-Admin-Token", ""))
        if domain not in interestedCandidates.DOMAINS:
            raise HTTPException(status_code=400, detail="Select dev, engineer, law, or dental.")
        try:
            result = interestedCandidates.list_interested(domain, limit, after, jd_id.strip())
            return JSONResponse(result, headers={"Cache-Control": "private, no-store", "Vary": "X-DevReady-Admin-Token"})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Saved candidate report is temporarily unavailable. Retry without running a provider search.") from exc

    return router

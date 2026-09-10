"""Protected, account-wide provider usage, independent of recruiter workspaces."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from peopleDataLabs import accountUsage


def create_router(require_admin_token):
    router = APIRouter(tags=["external", "provider usage"])

    @router.get("/external/provider-usage")
    def provider_usage(request: Request):
        require_admin_token(request.headers.get("X-DevReady-Admin-Token", ""))
        return JSONResponse(accountUsage.get_account_usage(), headers={"Cache-Control": "no-store"})

    return router

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request


BASE_URL = "http://127.0.0.1:8000"

PROFILES = {
    "dev": {"id": "2306", "name": "QA Tech Smoke 202605132214", "email": "qa.dev.202605132214@devready.example", "job_id": "18"},
    "engineer": {"id": "2307", "name": "QA Build Smoke 202605132214", "email": "qa.engineer.202605132214@devready.example", "job_id": "19"},
    "law": {"id": "2308", "name": "QA Legal Smoke 202605132214", "email": "qa.law.202605132214@devready.example", "job_id": "20"},
}


def request_json(path: str, method: str = "GET", form: dict | None = None, expect_error: bool = False):
    data = None
    headers = {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
            if expect_error:
                return {"ok": False, "status": response.status, "unexpected_success": parsed}
            return {"ok": True, "status": response.status, "data": parsed}
    except Exception as exc:
        if expect_error:
            return {"ok": True, "error": str(exc)}
        return {"ok": False, "error": str(exc)}


def main() -> int:
    results = {}
    overall_ok = True

    health = request_json("/api/health")
    results["health"] = health
    overall_ok = overall_ok and health["ok"]

    for domain, profile in PROFILES.items():
        domain_result = {}
        own = request_json(f"/api/azure/getProfile/{profile['id']}?domain={domain}")
        domain_result["profile_own_domain"] = own
        domain_result["portfolio_count"] = len((own.get("data") or {}).get("portfolioExperience") or []) if own["ok"] else 0
        domain_result["profile_domain"] = (own.get("data") or {}).get("domain") if own["ok"] else None
        domain_result["public_url"] = ((own.get("data") or {}).get("profile") or {}).get("publicUrl") if own["ok"] else None
        overall_ok = overall_ok and own["ok"] and domain_result["portfolio_count"] >= 1 and domain_result["profile_domain"] == domain

        domain_result["search_own_domain"] = request_json(
            "/api/azure/searchNameEmail",
            method="POST",
            form={"search_string": profile["email"], "domain": domain, "limit": "5"},
        )
        search_rows = domain_result["search_own_domain"].get("data") or []
        domain_result["search_own_count"] = len(search_rows)
        overall_ok = overall_ok and domain_result["search_own_domain"]["ok"] and any(str(row.get("id")) == profile["id"] for row in search_rows)

        cross_results = {}
        for other_domain in PROFILES:
            if other_domain == domain:
                continue
            cross_profile = request_json(f"/api/azure/getProfile/{profile['id']}?domain={other_domain}", expect_error=True)
            cross_search = request_json(
                "/api/azure/searchNameEmail",
                method="POST",
                form={"search_string": profile["email"], "domain": other_domain, "limit": "5"},
            )
            cross_rows = cross_search.get("data") or []
            cross_results[other_domain] = {
                "profile_blocked": cross_profile["ok"],
                "search_count": len(cross_rows),
            }
            overall_ok = overall_ok and cross_profile["ok"] and len(cross_rows) == 0
        domain_result["cross_domain"] = cross_results

        job = request_json(f"/api/azureJobs/getJob/{profile['job_id']}?domain={domain}")
        domain_result["job_own_domain"] = job
        overall_ok = overall_ok and job["ok"]
        for other_domain in PROFILES:
            if other_domain == domain:
                continue
            cross_job = request_json(f"/api/azureJobs/getJob/{profile['job_id']}?domain={other_domain}", expect_error=True)
            domain_result.setdefault("job_cross_domain", {})[other_domain] = cross_job["ok"]
            overall_ok = overall_ok and cross_job["ok"]

        time_admin = request_json(f"/api/time-entry/admin?domain={domain}&status=all")
        groups = ((time_admin.get("data") or {}).get("groups") or [])
        domain_result["time_admin_group_count"] = len([group for group in groups if str(group.get("profile_id")) == profile["id"]])
        overall_ok = overall_ok and time_admin["ok"] and domain_result["time_admin_group_count"] >= 1

        if domain_result["public_url"]:
            public = request_json(f"/api/azure/public/{domain_result['public_url']}")
            domain_result["public_profile_domain"] = (public.get("data") or {}).get("domain") if public["ok"] else None
            overall_ok = overall_ok and public["ok"] and domain_result["public_profile_domain"] == domain

        results[domain] = domain_result

    print(json.dumps({"ok": overall_ok, "results": results}, indent=2))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

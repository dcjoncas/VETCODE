# VETCODE Golden Copy

## Canonical restore point

The VETCODE Golden Copy is the complete, restored, live-verified development baseline created on July 14, 2026.

| Item | Value |
| --- | --- |
| Repository | `https://github.com/dcjoncas/VETCODE.git` |
| Immutable tag | `golden-vetcode-2026-07-14-lawyer-mining` |
| Backup branch | `backup/golden-vetcode-2026-07-14-lawyer-mining` |
| Commit | `6ac0b3aa11e60842d67223ccaf81b9c19e7cebbc` |
| Prior restore tag | `golden-vetcode-2026-07-14` |
| Railway project | `VETCODE` |
| Railway environment | `dev` |
| Railway service | `VETCODE` |
| Live URL | `https://vetcode-dev.up.railway.app/` |

The tag is the authoritative backup because it cannot drift with normal development. The backup branch exists for easy inspection in GitHub.

## What the Golden Copy protects

The restore point contains the complete VETCODE application at that commit, including the FastAPI backend, UI pages, shared navigation, candidate workflows, job descriptions, production PDL lawyer search, source auditing, California Bar verification links, external candidate search, and environment API.

It does not contain Railway secrets or database contents. Those remain managed by Railway and the connected data services.

## Verify lineage before new work

From a VETCODE checkout:

```powershell
git fetch origin --tags
git status --short --branch
git branch --show-current
git merge-base --is-ancestor golden-vetcode-2026-07-14-lawyer-mining HEAD
```

The final command must exit successfully. Continue development from the latest intended `Development` commit, not by resetting active work back to the tag.

## Complete Railway restore

Use a clean clone or clean worktree. Do not use a working directory containing uncommitted user changes.

```powershell
git clone https://github.com/dcjoncas/VETCODE.git VETCODE-golden-restore
Set-Location VETCODE-golden-restore
git checkout --detach golden-vetcode-2026-07-14-lawyer-mining
git rev-parse HEAD
python -m py_compile backend/main.py
railway link -p VETCODE -e dev -s VETCODE
railway status
railway up --detach
```

The reported commit must be:

```text
6ac0b3aa11e60842d67223ccaf81b9c19e7cebbc
```

Wait for Railway to report `SUCCESS`, then verify:

```powershell
curl.exe --max-time 20 -s -o NUL -w "root=%{http_code}`n" "https://vetcode-dev.up.railway.app/"
curl.exe --max-time 20 -s -o NUL -w "environment=%{http_code}`n" "https://vetcode-dev.up.railway.app/api/environment"
curl.exe --max-time 20 -s -o NUL -w "jobs=%{http_code}`n" "https://vetcode-dev.up.railway.app/ui/pages/job-descriptions.html?domain=law"
curl.exe --max-time 20 -s -o NUL -w "external=%{http_code}`n" "https://vetcode-dev.up.railway.app/ui/pages/mine-candidate-external.html?domain=law"
curl.exe --max-time 20 -s -o NUL -w "criteria=%{http_code}`n" "https://vetcode-dev.up.railway.app/api/azureJobs/external/criteria/85?domain=law"
```

All five results must be `200`. Review Railway logs for startup errors and missing assets before declaring recovery complete.

## Ongoing backup discipline

1. Inspect the whole affected workflow before changing code.
2. Commit and push intended changes to GitHub before deployment.
3. Confirm the Railway project, environment, service, working directory, branch, and commit.
4. Deploy only the complete VETCODE repository to the existing VETCODE service.
5. Run local checks and the four live smoke tests.
6. Create a dated restore tag before high-risk releases without moving the Golden Copy tag.

Standalone systems such as Vetcode DevCrew require their own Railway service and domain until an integration is deliberately implemented inside this repository.

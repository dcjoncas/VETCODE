# VETCODE Project Operating Rules

These instructions apply to every Codex task opened anywhere inside this repository.

## Project identity

- This repository is the complete `dcjoncas/VETCODE` application, not a single page or standalone experiment.
- The active Railway development target is `Project: VETCODE`, `Environment: dev`, `Service: VETCODE`.
- The active development branch is `Development`. Do not assume `main` is the current deploy branch.
- Read the relevant application code and shared components before changing a page. VETCODE behavior spans `backend/main.py`, `backend/ui`, data integrations, and shared navigation.

## Golden Copy

- Immutable Git tag: `golden-vetcode-2026-07-14`
- Backup branch: `backup/golden-vetcode-2026-07-14`
- Baseline commit: `90f5c3f3e68c3e7eb8c4be09ea2225840772fddd`
- Recovery guide: `docs/GOLDEN_COPY.md`

The Golden Copy is the restored, live-verified VETCODE baseline. Never move, recreate, force-push, or delete the Golden Copy tag or backup branch unless Darrin explicitly directs it.

## Required start-of-task checks

Before editing, testing, committing, or deploying:

1. Confirm the repository root, active branch, remotes, and worktree status.
2. Read this file and `docs/GOLDEN_COPY.md`.
3. Preserve all existing tracked and untracked user changes.
4. Inspect the complete workflow affected by the request, including backend routes, page code, shared components, and data dependencies.
5. Confirm new work descends from the Golden Copy and the latest intended `Development` branch.

A new chat does not create a new application. It continues work on the complete VETCODE project.

## Deployment safeguards

- Never deploy an unrelated repository or standalone app to the existing VETCODE Railway service.
- In particular, never deploy `vetcode-devcrew`, a prototype, or another working directory over `VETCODE / dev / VETCODE`.
- Before `railway up`, confirm `railway status`, the current Git commit, the working directory, and the intended branch.
- Back up intended application changes to GitHub before deployment unless Darrin explicitly requests a local-only experiment.
- Use a clean commit or clean temporary worktree for recovery deployments. Never restore from a dirty working tree.
- A successful Railway build only proves that a container started. It does not prove the correct VETCODE application was deployed.

After every VETCODE deployment, verify at minimum:

- `/`
- `/api/environment`
- `/ui/pages/job-descriptions.html?domain=law`
- `/ui/pages/mine-candidate-external.html?domain=law`

All four routes must return HTTP 200 before reporting the deployment complete.

## Change and recovery policy

- Continue normal development from the latest intended `Development` commit that descends from the Golden Copy.
- Create new restore points before high-risk releases; do not redefine the Golden Copy automatically.
- Builders cannot self-approve. Run focused tests and validate live behavior in proportion to the change.
- If a deployment removes existing routes or replaces the application, stop, identify the last known-good Git commit, and follow `docs/GOLDEN_COPY.md`.

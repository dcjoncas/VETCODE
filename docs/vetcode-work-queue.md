# VETCODE Work Queue

Moved from the old mock/AICERT project conversations into VETCODE.

## Project Boundary

All recruiting, profile, job, candidate chat, certification, and domain logic belongs in the VETCODE codebase and the VETCODE Railway project.

Other projects should not carry active VETCODE implementation work:

- `mock`: no VETCODE product work
- `AICERT`: no separate certification deployment for VETCODE domains

## Domains

VETCODE has three isolated domains:

- Technology: DevReady
- Law: LegalReady
- Engineering: BuildReady

Every profile, resume, job description, match, certification path, question bank, chat, and generated artifact must belong to exactly one domain.

Changing domains must clear or reload domain-scoped screen state. No candidate, job description, certification path, or profile preview should remain visible after switching to another domain unless it belongs to the newly selected domain.

## Queued Requirements

- Candidate chat must be deterministic: one accepted answer, one saved score, one next survey question.
- Candidate chat must not loop on generic responses such as "Thanks, noted".
- Resume uploads must tag the created profile to the active domain.
- Job description uploads and job searches must tag and filter by the active domain.
- Profile search and find-candidate flows must show only candidates in the active domain.
- Domain switching must refresh or clear profile/job/certification state.
- Certification paths must be domain-specific and must not require a separate domain choice after the user is already in a domain.
- Certification link is broken and must be fixed inside VETCODE.
- Remove dangerous cross-domain or global options.
- Add a "Missing skills for this job" action that lets the user select a person and list missing skills/requirements against the selected job description.

## Current Focus

Fix domain isolation first, then candidate chat determinism, then certification routing.

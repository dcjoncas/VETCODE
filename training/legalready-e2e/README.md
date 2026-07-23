# LegalReady End-to-End Training Kit

Use `LegalReady-End-to-End-Training-Guide.docx` as the trainer script. The exercise follows the live Law workflow from job description through candidate intake, matching, vetting, client communication, candidate review, client interview, and Status verification.

The live platform now has a separate **Platform Training** page at `/ui/pages/legalready-training.html?domain=law`. It starts in Talent and walks a trainee through finding a candidate internally or externally, completing the profile, selecting the JD, matching, shortlisting, confirming interest, scheduling both calls, and onboarding. This guide is intentionally separate from Mobile Modules, which remains the phone-first user app.

## Files trainees use

- `Sample-JD-Legal-Operations-eDiscovery-Analyst.docx`
- `Sample-Resume-Jordan-Ellis-Mitch.docx` routes all training identities to `mitch.blake@legalready.io`
- `Sample-Resume-Jordan-Ellis-Michael.docx` routes all training identities to `michael.shrader@legalready.io`
- `Sample-Resume-Jordan-Ellis-Kacey-Jo.docx` routes all training identities to `kacey-jo.hyde@legalready.io`

Candidate, client, interviewer, and attendee fields must all use the trainee's own LegalReady address. Display names may differ so trainees can see which role each message represents.

## Proven result

The live validation run created JD `88`, generated/updated profile `2368`, returned the candidate with a match score of `63` against a required training threshold of `50`, generated both AI email drafts, archived both schedule handoffs, and read both records back successfully.

Evidence is in `results/legalready-e2e-latest.html` and `results/legalready-e2e-latest.json`.

The automated runner never calls `/api/calendar/invite/create`; it creates drafts and `training-no-send` archive records only. No outside email or calendar invitation was sent.

## Optional repeatable proof run

`run_legalready_e2e.py` accepts a trainee name, email, matching resume file, and `--execute`. Without `--execute`, it performs read-only page and health checks. With `--execute`, it creates training records, generates drafts, archives the two schedule records, and verifies them. It still does not send an invitation.

## Current email limitation

The three LegalReady aliases exist in Microsoft 365, but incoming delivery through those aliases cannot be confirmed until `legalready.io` DNS mail routing is moved to Microsoft 365. The training exercise remains safe because its validated mode does not send mail.

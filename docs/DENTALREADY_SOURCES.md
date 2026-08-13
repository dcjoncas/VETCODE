# DentalReady Sourcing Notes

DentalReady is the VETCODE tenant for dental assistants, registered dental hygienists, expanded-functions dental assistants, treatment coordinators, dental front-office staff, sterile processing support, orthodontic assistants, and specialty dental clinic roles.

## Current VETCODE Integration Path

- Use `domain=dental` for all DentalReady profile, job-description, shortlist, onboarding, Atlas, accounting, and reporting data.
- Use the existing external sourcing providers for early discovery:
  - People Data Labs for professional discovery when `PDL_API_KEY` is configured.
  - Coresignal for professional-profile comparison when `CORESIGNAL_API_KEY` is configured.
  - Brave Search for public-web research only when `BRAVE_SEARCH_API_KEY` is configured.
- Do not use CourtListener for DentalReady. CourtListener remains LegalReady-only.
- Do not use GitHub as a DentalReady source signal. Public code footprint is not meaningful for dental staffing.

## Dental Industry Sources To Evaluate

- ADA CareerCenter: broad dental jobs marketplace. Public pages expose job search/posting and employer advertising, but no public candidate API was found. Treat as an employer portal/job-board channel.
- DentalPost: dental-focused job board for assistants, hygienists, dentists, and front-office staff. Public materials describe job posting, screening, scheduling, tracking, and messaging inside the employer app; no public self-serve API was found. Treat as a likely employer/partner workflow unless a private integration is negotiated.
- American Dental Assistants Association Career Center: dental assistant job-posting channel. The career center appears to be a managed association job-board portal; no public candidate API was found.
- DANB: useful for reaching certificants/certificate holders through listed employer outreach options. DANB's public employer path is list rental or sponsored email, not an API.
- Toothio: dental temp/full-time staffing platform. No public API was found; use practice account/demo/partner path and import only candidate details explicitly provided to VETCODE.
- Stynt: dental recruitment and job-board platform. No public API was found; use sales/partner path if VETCODE needs structured candidate handoff.
- DentistJobCafe: dental job board with employer/recruiter services and resume search. No public API was found; use employer account/demo path for resume database access.
- iHireDental and Jobley: dental/healthcare job board options to evaluate for distribution or candidate pipeline.

## Current API Verdict

- Available API-style integrations now surfaced in DentalReady:
  - People Data Labs: candidate/professional discovery when `PDL_API_KEY` is configured.
  - Coresignal: professional-profile comparison when `CORESIGNAL_API_KEY` is configured.
  - Brave Search: public-web research when `BRAVE_SEARCH_API_KEY` is configured; results remain research-only.
- Dental-specific boards currently added to the DentalReady source directory as portal/partner/manual channels:
  - ADA CareerCenter
  - DentalPost
  - ADAA Career Center
  - DANB
  - Toothio
  - Stynt
  - DentistJobCafe
- Do not scrape logged-in candidate databases. If a vendor provides exports, webhook access, a partner API, or email/applicant forwarding, import those leads into TEMP profiles with source notes and manual verification.

## Verification Rules

- Treat all imported or externally discovered DentalReady candidates as TEMP until reviewed.
- Verify role credentials, state requirements, licenses/certifications, infection-control training, and current availability before outreach or permanent profile promotion.
- Do not infer a license from a job title alone. Store verification evidence in profile notes or workflow history.

## Starter Role Families

- Registered Dental Hygienist
- Expanded Functions Dental Assistant
- Chairside Dental Assistant
- Orthodontic Assistant
- Treatment Coordinator
- Dental Front Office Coordinator
- Sterile Processing Technician
- Specialty Dental Assistant: endodontics, oral surgery, implant, prosthodontics, pediatric dentistry

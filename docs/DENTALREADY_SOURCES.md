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

- DentalPost: dental-focused job board for assistants, hygienists, dentists, and front-office staff. Treat as a likely employer/partner workflow unless a private integration is negotiated.
- American Dental Assistants Association Career Center: dental assistant job-posting channel.
- DANB: useful for reaching certificants/certificate holders through listed employer outreach options.
- ADA CareerCenter: broad dental jobs marketplace.
- Toothio: dental temp/full-time staffing platform.
- Stynt: dental recruitment and job-board platform.
- DentistJobCafe: dental job board with employer/recruiter services and resume search.
- iHireDental and Jobley: dental/healthcare job board options to evaluate for distribution or candidate pipeline.

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

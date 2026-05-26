# VETCODE User Guide

Updated: 2026-05-25

## What VETCODE Is

VETCODE is the operating workspace for moving candidates through a domain-specific talent workflow. It supports three separate workspaces:

| Domain | Brand | Purpose |
| --- | --- | --- |
| Technology | DevReady | Technology, AI, data, software, product, and platform roles |
| Engineering | BuildReady | Civil, project, controls, infrastructure, and engineering delivery roles |
| Law | LegalReady | Legal operations, compliance, contract, regulatory, and law-adjacent roles |

Each domain should be treated as its own workspace. Profiles, resumes, job descriptions, certifications, badges, client records, onboarding, time, and reports should stay inside the active domain.

## Core Rule

Start with the domain, then keep everything inside that domain.

Do not use a Technology profile for an Engineering role, do not show Law badges in Technology, and do not start onboarding until the candidate has a completed profile.

## Main Web App

Local development URL:

`http://127.0.0.1:8000/ui/pages/find-candidate.html`

Railway dev URL:

`https://vetcode-dev.up.railway.app/ui/pages/find-candidate.html`

Mobile URL:

`https://vetcode-dev.up.railway.app/ui/mobile.html`

## Navigation Overview

The left navigation is organized around the hiring workflow:

| Navigation Link | What It Is For |
| --- | --- |
| Talent | Start point for candidate search, resume upload, job context, and shortlist setup |
| Job Descriptions | Create, upload, edit, save, and select role descriptions for the active domain |
| Find Candidates (In) | Match saved profiles already inside the active domain against a selected job |
| Find Candidates (Out) | Search outside sources, create temporary profiles, and promote selected people |
| Profiles | Review, edit, shortlist, publish, and inspect candidate profile details |
| Meet | Record or manage meeting outputs and move important notes into CRM |
| Interviews | Schedule candidate reviews and client interviews |
| Onboarding | Create onboarding links from completed profiles and monitor paperwork status |
| Time | Review submitted time, approve it, process it, and connect it to accounting |
| Status | Track where candidates are in the workflow |
| Atlas | Manage client teams, client contacts, deals, touches, and sales follow-up |
| Prospect Reference | Search passive prospect companies and promote useful companies into Atlas |
| Reports | Run operational reports across jobs, profiles, time, onboarding, invoices, and status |
| Accounting | Manage resource rates, costs, invoice inputs, and accounting setup |
| Invoices | Build customer invoices from approved billable time |
| Certification | Send AI certification links and track certification handoff |
| Test Challenge | Send or review technical challenge evidence |
| View Badges | View domain-specific badge catalog and badge progression |
| Meridian | Additional workspace link reserved for Meridian features |
| Agents | Configure page-aware helper agents |
| Admin | Manage users, menus, candidate access, and operational settings |

## The Standard Candidate Workflow

### 1. Select The Domain

Use the domain selector at the top of the page or in the process bar:

- Technology Domain
- Engineer Domain
- Law Domain

The page colors should change with the domain:

- Technology: green
- Engineering: blue
- Law: brown/gold

If you switch domains, confirm the page refreshed and the selected job/candidate belongs to that domain.

### 2. Add Or Choose A Job Description

Go to `Job Descriptions`.

You can:

- Paste a job description.
- Upload a PDF, DOCX, TXT, or Markdown job file.
- Normalize and save the job.
- Edit an existing saved job.
- Click `Use JD` to make a job active for matching.

Saved job descriptions are grouped by client. On the job card:

- `Use JD` selects it for the matching workflow.
- `Edit` opens it for changes.
- `View` opens the full detail.
- `Delete` removes it.

If you create a job description, it should automatically become available on Talent, Find Candidates (In), and Find Candidates (Out).

### 3. Find Candidates Already In The Domain

Go to `Find Candidates (In)`.

Use this when the candidate should already exist in the system.

Typical steps:

1. Select or confirm the active job description.
2. Search profiles or list all profiles.
3. Check 2-3 candidate matches.
4. Add checked candidates to the shortlist.
5. Continue to candidate review or shortlist.

Use this screen for internal matching and candidate ranking.

### 4. Find Candidates Outside The Domain

Go to `Find Candidates (Out)`.

Use this when internal profiles are weak or missing.

This area can search outside sources such as People Data / external search, create temporary profiles, and let you choose which external candidates should become part of the normal shortlist workflow.

Important:

- Temporary profiles should be reviewed before being made permanent.
- Duplicate emails must be avoided.
- Empty emails should not create permanent profiles that collide with other blank-email records.
- Imported candidates must be tagged to the active domain.

### 5. Review The Profile

Go to `Profiles`.

A candidate profile includes:

- Name, title, location, and image.
- About/summary.
- Skills and skill years.
- Developer or professional personality.
- Cultural / domain experience.
- Portfolio / work history.
- Public profile link.
- Original resume when available.

Profile buttons:

| Button | Purpose |
| --- | --- |
| Load Known Profile | Load an existing profile by profile id or selection |
| View TEMP Profiles | Review temporary sourced profiles |
| Public Profile | Open the client-safe public profile |
| Shortlist | Add this person to the active shortlist |
| Edit Profile | Edit profile values |
| Original Resume | Open the resume source if available |

If a profile says `Profile partially complete`, finish the missing pieces before onboarding.

### 6. Complete The Candidate Chat / Personality Steps

Candidate chat collects personality and culture answers.

The candidate should see a question countdown so they know how many questions remain. If the candidate partially completes the chat, progress should be saved and reflected on the profile.

The profile is not considered onboarding-ready until:

- Regular profile data exists.
- Personality survey data exists.
- Culture profile data exists.

### 7. Candidate Review Before Client Shortlist

Before sending a candidate to a client shortlist, conduct a candidate review.

This is not a formal interview. It is a community/member touch base to confirm:

- The candidate understands the job description.
- They are comfortable with the work activities.
- Timing and availability are realistic.
- Any skill gaps are known and can be explained.
- The candidate agrees to be presented.

The app should help show what the candidate is short on compared with the job description.

### 8. Shortlist / Client Communication

The shortlist page packages candidates for client communication.

Recommended order:

1. Generate email.
2. Attach profiles.
3. Send to client.

Use the client communication step only after candidate review is complete or intentionally skipped.

### 9. Schedule Interviews

Go to `Interviews`.

There are two interview types:

| Type | Required Details |
| --- | --- |
| Candidate Review | Candidate, candidate interviewer name, interviewer email |
| Client Interview | Candidate, client company, client contact name, client email |

Use the shaded panels to confirm which interview type you are scheduling.

Calendar integrations:

- Google Calendar
- Outlook Calendar
- Calendly fallback / link workflow

If OAuth is missing, the app will show a missing configuration message.

### 10. Start Onboarding

Go to `Onboarding`.

Onboarding must derive from a completed profile. Do not manually type in candidate data when a profile should supply it.

To create an onboarding link:

1. Select the completed profile.
2. Confirm candidate name, email, role/title, and start date.
3. Add HR note if needed.
4. Create onboarding link.
5. Copy/send the onboarding link to the candidate.

The onboarding record also creates or exposes a time-entry link.

### 11. Candidate Time Entry

Candidates or consultants submit time through a time-entry link.

The link should know:

- Candidate / consultant.
- Client.
- Project or role.
- Week starting.
- Hours per day.
- Description per day.

Free typing should be limited to description fields. Names, customers, roles, rates, and linked entities should come from dropdowns or linked records.

### 12. Time Admin

Go to `Time`.

Time Admin lets HR or operations:

- See weekly submitted time.
- Filter by week and status.
- See total hours by candidate.
- Review time details.
- Approve time.
- Mark time as processed.
- See processed hours and open hours.
- Link back to profile and client context.

Statuses:

| Status | Meaning |
| --- | --- |
| Submitted | Candidate entered time; not approved yet |
| Needs review | Someone must inspect the row before approval |
| Approved | Ready for payroll/invoice processing |
| Processed | Payroll/invoice/accounting action has been completed |

Approved time feeds invoicing.

### 13. Accounting And Invoices

Accounting is where resource rates and billing information are managed.

Important concepts:

- A customer must come from Atlas.
- A consultant must come from onboarding/resource setup.
- Bill rate and cost rate must be linked to the onboarded resource.
- Invoices are built from approved billable time.
- Invoice customer details come from Atlas.
- Accounting resources, invoices, and expenses are stored in database tables.

Invoice workflow:

1. Select customer.
2. Select invoice period.
3. Confirm approved time rows.
4. Refresh preview.
5. Save invoice.
6. Send invoice.
7. Track sent, viewed/read, due, overdue, paid, or void status.

If the page says no approved billable time exists, go to Time and approve time first or go to Accounting and add the consultant bill rate.

### 14. Atlas

Atlas tracks clients, contacts, deals, and touches.

Terminology:

- Team card = client/company account.
- Client contact = actual person at that company.
- Deal = opportunity or contract record connected to the client.

Client contacts should be real people, not generic entities like “Hiring Team.”

A client contact can include:

- Name.
- Title / job title.
- Relationship role: decision maker, influencer, buyer, finance, technical reviewer, etc.
- Email.
- Phone.
- LinkedIn URL.
- Picture URL.
- Last conversation.
- Notes.

Use `Record Touch` for:

- Call.
- Email.
- Meeting.
- LinkedIn.
- Other follow-up.

Atlas news scan checks web/news signals for interesting customer updates.

Atlas cards are stored in Postgres and separated by domain. Prospect Reference is a separate passive library and does not create an Atlas card until a prospect is promoted.

### 15. Meet

Meet handles meeting recordings, saved outputs, transcripts, PDF outputs, Q&A, and CRM handoff.

Use it when:

- You recorded a client or candidate meeting.
- You need a summary.
- You want to move notes into CRM.
- You want to ask questions about a saved meeting.

Saved outputs should be collapsed into drill-down sections so the page stays manageable.

### 16. Reports

Reports provides drill-down reporting across the business.

Report areas include:

- Job descriptions: active/inactive, totals, stale roles.
- Profiles: totals, completed, assignment status.
- Candidate in/out totals.
- Meetings by day/week and participants.
- Interviews scheduled by client/candidate type.
- Onboarding status.
- Time entry for the week and missing time.
- Accounting and invoices.
- Company status overview.

Reports should link back into the process, such as:

- Missing time -> open profile, client, or reminder workflow.
- Overdue invoice -> open invoice.
- Stale job -> open job description.
- Incomplete profile -> open profile completion.

### 17. Certifications, Badges, And Test Challenge

Certification and badges are domain-specific.

The app should not show Technology or Law certification paths when the active domain is Engineering.

Use:

- `Certification` to create/send candidate certification links.
- `View Badges` to see badge catalog for the current domain.
- `Test Challenge` to manage technical challenge evidence.

The main internal app should not show the candidate exam itself. Candidates should receive a separate link.

### 18. Admin

Admin is for:

- User access.
- Menu permissions.
- Candidate access search.
- Resend login.
- Block.
- Delete or mark deleted.
- Operational cleanup.
- Local workflow reset.
- Agent controls.

Admin actions should be used carefully. Deleting and blocking can affect access.

### 19. Agents

Numa is the main page-aware assistant. Numa changes focus based on the page: Talent, Profiles, CRM, Scheduling, Accounting, etc.

Egeria is the sidebar helper agent entry point.

Agents should be page-aware and process-aware. Their job is to help the user move a candidate, client, job, invoice, or time record through the system without losing domain context.

Agents should not expose secrets, perform destructive operations without confirmation, or claim they changed records unless the app confirms it.

### 20. Mobile App

Mobile URL:

`/ui/mobile.html`

The mobile app is intentionally simple. It is not a replacement for the full web app.

Mobile supports:

- Select domain.
- Select saved job or starter role.
- Select completed candidate profile.
- Create onboarding link.
- Copy onboarding link.
- Copy time-entry link.
- View recent onboarding records.

Use the full app for:

- Uploading resumes.
- Creating detailed job descriptions.
- CRM.
- Reports.
- Accounting.
- Invoices.
- Certification management.
- Admin.

## Common Tasks

### Create A Candidate From A Resume

1. Go to Talent.
2. Select the correct domain.
3. Upload the resume.
4. Wait for profile generation.
5. Open the profile.
6. Confirm work history and portfolio rows.
7. Complete personality/culture if missing.
8. Shortlist or continue matching.

### Create A Job And Match Candidates

1. Go to Job Descriptions.
2. Paste or upload the job.
3. Normalize and save.
4. Click `Use JD`.
5. Go to Find Candidates (In).
6. Search/list profiles.
7. Add best candidates to shortlist.
8. Review candidate gaps.
9. Conduct candidate review.
10. Send client shortlist.

### Start Onboarding

1. Open Onboarding.
2. Select completed profile.
3. Confirm start date.
4. Create onboarding link.
5. Send candidate link.
6. Confirm record appears in onboarding list.
7. Confirm time-entry link exists.

### Approve Time And Invoice A Customer

1. Open Time.
2. Filter the week.
3. Review submitted rows.
4. Approve rows.
5. Open Accounting and confirm resource rates.
6. Open Invoices.
7. Select CRM customer and period.
8. Select approved time rows.
9. Save invoice.
10. Send invoice.
11. Track status.

## Troubleshooting

### Candidate Does Not Appear In Search

Check:

- Correct domain selected.
- Profile exists in that domain.
- Profile has an email/name.
- Search limit is not too low.
- Candidate is not only a temporary profile.

### Job Does Not Appear In Matching

Check:

- Job was saved.
- Job domain matches current domain.
- `Use JD` was clicked.
- Page was refreshed after domain switch.

### Onboarding Button Is Blocked

The profile is probably incomplete. Complete:

- Regular profile.
- Personality survey.
- Culture profile.

### Invoice Says No Billable Time

Check:

- Time was submitted.
- Time was approved.
- Resource has bill rate.
- Customer is selected from CRM.
- Invoice period includes approved time rows.

### Calendar Says OAuth Is Missing

The local or Railway environment is missing Google or Outlook OAuth variables. Contact the technical owner.

### Domain Colors Or Data Look Wrong

Refresh the page and confirm the domain dropdown. If it still shows wrong data, clear local workflow state from Admin or start a fresh browser tab with `?domain=dev`, `?domain=engineer`, or `?domain=law`.

## Glossary

| Term | Meaning |
| --- | --- |
| Domain | One workspace: Technology, Engineering, or Law |
| Profile | Candidate record with resume, skills, culture, personality, portfolio |
| JD | Job description |
| Shortlist | Candidate list being prepared for client review |
| Candidate Review | Internal/community touch base before client presentation |
| Client Interview | Interview with client contact |
| Onboarding | HR paperwork and start process after hire decision |
| Time Entry | Weekly consultant hours submitted through candidate/staff link |
| Processed Time | Time that has been handled by HR/payroll/invoice process |
| Team Card | CRM client/company card |
| Client Contact | Real person at the client company |
| Numa | Main page-aware app assistant |
| Egeria | Sidebar helper agent entry point |

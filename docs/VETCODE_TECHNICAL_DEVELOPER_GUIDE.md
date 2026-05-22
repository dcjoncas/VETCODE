# VETCODE Technical Developer Guide

Updated: 2026-05-21

## Purpose

This guide explains how the VETCODE application is structured, where code lives, how the AI features work, how data is stored, what the database schema looks like, and how to run, test, and deploy the system.

VETCODE currently combines:

- FastAPI backend.
- Static HTML/CSS/JavaScript frontend.
- Azure PostgreSQL candidate/job/profile database.
- Local SQLite fallback databases.
- JSON operational stores for workflow, CRM, accounting, time, onboarding, badges, and demo fixtures.
- OpenAI-powered extraction, agent, summarization, certification, chat, and scheduling support.
- Railway dev deployment.

## Repository Layout

Root:

| Path | Purpose |
| --- | --- |
| `backend/` | Main FastAPI app, API routes, UI assets, storage, AI modules |
| `backend/main.py` | Primary application entry point and most app-specific APIs |
| `backend/calendar_router.py` | Google/Outlook calendar OAuth, invite draft, invite creation |
| `backend/storage.py` | Local SQLite profile/JD fallback store |
| `backend/azureUtils/` | Azure PostgreSQL storage and API routers |
| `backend/openAI/` | OpenAI/Numa/chat/job/email/page-agent logic |
| `backend/ui/` | Static frontend served at `/ui` |
| `backend/ui/pages/` | Main app pages |
| `backend/ui/pages/components/` | Shared sidebar, process flow, search bar |
| `backend/ui/pages/JS/` | Shared frontend JavaScript helpers |
| `backend/ui/assets/` | Domain CSS and logos |
| `data/demo_lifecycle_fixtures/` | Deployable demo JSON fixtures |
| `docs/` | Project documentation and QA artifacts |
| `scripts/` | Seeding, copy, and QA scripts |
| `requirements.txt` | Python dependencies |

Legacy/static experiments also exist under root `frontend/`, `ui/`, and `static/`. The actively deployed app is under `backend/`.

## Runtime Entry Point

The app is served from:

`backend/main.py`

Static frontend mount:

```python
app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")
```

Root redirect:

```text
/ -> /ui/index.html
/{page_name}.html -> /ui/pages/{page_name}.html
```

Local command:

```powershell
cd C:\Users\darri\Documents\GitHub\VETCODE\backend
.\.venv\Scripts\activate
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Railway deploy command from repo root:

```powershell
cd C:\Users\darri\Documents\GitHub\VETCODE
railway status
railway up
```

Current Railway dev service:

| Setting | Value |
| --- | --- |
| Project | `VETCODE` |
| Environment | `dev` |
| Service | `VETCODE` |
| Public domain | `vetcode-dev.up.railway.app` |
| Runtime port | `8080` |

## Domains

VETCODE has three domain keys:

| Domain Key | Brand | Storage Domain Value | Color |
| --- | --- | --- | --- |
| `dev` | DevReady / Technology | `technology` in some profile/JD records | Green |
| `engineer` | BuildReady / Engineering | `engineer` | Blue |
| `law` | LegalReady / Law | `law` | Brown/gold |

Relevant code:

- `backend/main.py`
  - `_domain_key`
  - `_storage_domain`
  - `_domain_db_path`
  - `_jd_db_path`
  - `_profile_db_path`
- `backend/ui/assets/devStyles.css`
- `backend/ui/assets/engineerStyles.css`
- `backend/ui/assets/lawStyles.css`

Important behavior:

- UI pages read `?domain=...` first.
- Domain is also saved in `sessionStorage.domain`.
- Domain-specific CSS is swapped using the domain key.
- Domain isolation must be preserved across profiles, JDs, certification paths, badges, time, accounting, CRM, and reports.

## Frontend Page Map

Active pages live in `backend/ui/pages/`.

| Page | Purpose |
| --- | --- |
| `find-candidate.html` | Talent intake, saved profile search, resume upload, job context |
| `job-descriptions.html` | JD upload, paste, normalize, edit, save, use JD |
| `match-role.html` | Internal candidate matching against active JD |
| `mine-candidate-external.html` | External sourcing and temporary profile import |
| `profile-preview.html` | Candidate profile review, shortlist, public profile |
| `profile-preview-edit.html` | Profile editing |
| `profile-public.html` | Client-safe public profile |
| `candidate-chat.html` | Candidate personality/culture chat |
| `client-comm.html` | Shortlist/client communication workflow |
| `schedule-interview.html` | Candidate review and client interview scheduling |
| `status-tracker.html` | Workflow status |
| `onboarding-admin.html` | HR onboarding link creation and status |
| `onboarding.html` | Candidate-facing onboarding form |
| `time-admin.html` | Time review, approval, processing, reports |
| `time-entry.html` | Candidate/staff time-entry form |
| `accounting.html` | Resource rates, costs, accounting setup |
| `invoices.html` | Invoice workbench and invoice status |
| `crm.html` | CRM client team cards, contacts, deals, touches |
| `sales-crm.html` | Sales-rep-specific CRM portal |
| `meet.html` | Meeting recording/output/CRM handoff |
| `reports.html` | Operational reports |
| `ai-cert.html` | Certification link management |
| `badge-catalog.html` | Domain-specific badges |
| `test-challenge.html` | Technical challenge workflow |
| `admin.html` | Admin users, access, settings |
| `agents.html` | Page-aware agent configuration |
| `mobile.html` | Simple mobile pick-and-onboard flow |

Shared UI:

| File | Purpose |
| --- | --- |
| `components/sidebar.html` | Loads sidebar, access rules, hints, Numa/Egeria surfaces |
| `components/sideNav.html` | Navigation definitions and grouping |
| `components/processFlow.html` | Process flow bar, FastBoard actions |
| `components/searchBar.html` | Global search |
| `JS/shortlist.js` | Browser-session shortlist state |
| `JS/pageAgents.js` | Frontend agent context and Numa behavior |
| `JS/profileCompletion.js` | Profile completion utilities |
| `JS/updateProcessFlow.js` | Process flow update helpers |
| `JS/apiScripts.js` | General API helper scripts |

## Backend Route Map

### Core Health, Environment, And Static

| Method | Path | Function |
| --- | --- | --- |
| GET | `/api/health` | `health` |
| GET | `/api/environment` | `environment` |
| GET | `/api/debug/dbinfo` | `dbinfo` |
| GET | `/` | `root` |
| GET | `/{page_name}.html` | `legacy_page_redirect` |

### Access And Admin

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/access/menu` | Return current/default menu config |
| POST | `/api/access/login` | Login user |
| POST | `/api/access/admin-login` | Admin login |
| POST | `/api/access/admin-check` | Validate admin token |
| POST | `/api/access/register` | Register access user |
| GET | `/api/admin/users` | List users |
| POST | `/api/admin/users` | Create/update user |
| POST | `/api/admin/users/{user_id}/block` | Block/unblock user |
| DELETE | `/api/admin/users/{user_id}` | Delete user |
| POST | `/api/admin/users/{user_id}/send-login` | Generate login info |
| GET | `/api/admin/candidates/search` | Search candidate access |
| POST | `/api/admin/candidates/access` | Block/delete candidate access |

### Profiles And Resumes

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/resume/upload` | Local/fallback resume upload |
| POST | `/api/resume/bulk_upload` | Bulk upload resumes |
| GET | `/api/profiles` | List local profiles |
| POST | `/api/profiles/skillSearch` | Local skill search |
| GET | `/api/profiles/{profile_id}` | Local profile detail |
| GET | `/api/profiles/{profile_id}/html` | Render profile HTML |
| GET | `/api/profiles/{profile_id}/docx` | Export profile DOCX |
| GET | `/api/profile/list` | Domain profile list |
| GET | `/api/profile/count` | Domain profile count |
| GET | `/api/profile/count/recent` | Recent profile count |
| POST | `/api/profile/search` | Profile search |
| POST | `/api/profile/pageCount` | Profile search count |
| POST | `/api/profile/pageSearch` | Paged profile search |
| GET | `/api/profile/{profile_id}` | Profile detail |

Azure router profile routes in `backend/azureUtils/routes/azureEndpoints.py` include:

- `/skills`
- `/skills/{searchQuery}`
- `/countCandidates`
- `/countCandidates/recent`
- `/countCandidates/status`
- `/countCandidates/all`
- `/profile/discovery`
- `/profiles/alphabetical`
- `/searchNameEmail`
- `/searchSkills`
- `/pageCount`
- `/pageSearch`
- `/getProfile/{profileId}`
- `/profile/completionStatus/{profileId}`
- `/public/{profileUrl}`
- `/public/getPublicUrl/{profileId}`
- `/getProfile/short/{profileId}`
- `/getProfile/short/score/{jobId}`
- `/profile/update`
- `/profile/updateSkills`
- `/profile/updateFeatures`
- `/profile/updatePortfolio`
- `/resume/upload`
- `/resume/{profileId}`

### Job Descriptions And Matching

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/jd/upload` | Upload JD file |
| POST | `/api/jd/normalize` | Normalize pasted JD |
| GET | `/api/jd/list` | List local JDs |
| GET | `/api/jd/{jd_id}` | Get local JD |
| GET | `/api/jd/{jd_id}/html` | Render JD HTML |
| GET | `/api/jd/{jd_id}/docx` | Export JD DOCX |
| GET | `/api/jd/latest` | Latest or selected JD |
| POST | `/api/match/run` | Run matching |
| POST | `/api/match/scorecard` | Build match scorecard |
| POST | `/api/match/interview_questions` | Generate interview questions |
| POST | `/api/match/explain` | Explain match/gaps |
| GET | `/api/match/report/html` | Match report HTML |
| GET | `/api/match/report/docx` | Match report DOCX |

Azure job router in `backend/azureUtils/routes/azureJobEndpoints.py` includes:

- `/createJob`
- `/uploadJob`
- `/updateJob/{jobId}`
- `/list/{domain}/{amount}`
- `/list/search/{domain}/{query}/{amount}`
- `/getJob/{jobId}`
- `/deleteJob/{jobId}`
- `/match/run`
- `/external/search`
- `/external/search-direct`
- `/external/import`
- `/external/temp`
- `/external/temp/{person_id}/make-permanent`
- `/external/temp/{person_id}`

### Workflow, Interviews, Onboarding, And Time

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/workflow/events` | Record workflow event |
| GET | `/api/workflow/events/{profile_id}` | Profile workflow events |
| POST | `/api/interviews/archive` | Save interview archive record |
| GET | `/api/interviews/archive` | List interview archive |
| POST | `/api/onboarding/start` | Create onboarding link from completed profile |
| GET | `/api/onboarding/admin` | List onboarding records |
| GET | `/api/onboarding/candidates` | Completed profiles ready for onboarding |
| GET | `/api/onboarding/{token}` | Candidate onboarding record |
| POST | `/api/onboarding/{token}` | Submit onboarding form |
| POST | `/api/time-entry` | Submit weekly time |
| GET | `/api/time-entry/admin` | Admin time report |
| POST | `/api/time-entry/{entry_id}/status` | Update time status |
| GET | `/api/time-entry/{token}` | Candidate/staff time records |

### CRM, Sales CRM, Meetings

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/crm/records` | CRM account/contact/deal records |
| POST | `/api/crm/news-scan` | Web/news scan for customer updates |
| GET | `/api/sales-crm/portal` | Sales rep portal data |
| POST | `/api/sales-crm/account` | Update sales CRM account |
| GET | `/api/meetings/archive` | Saved meeting outputs |

### Accounting And Invoices

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/accounting/summary` | Domain accounting report data |
| POST | `/api/accounting/resource` | Save resource bill/cost rates |
| POST | `/api/accounting/invoice` | Save invoice |
| POST | `/api/accounting/invoice/{invoice_id}/status` | Update invoice status |
| GET | `/api/invoices/workbench` | Invoice workbench data |
| POST | `/api/invoices/from-time` | Create invoice from approved time |

### Calendar

Defined in `backend/calendar_router.py`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/calendar/health` | Calendar provider status |
| GET | `/auth/google` | Start Google OAuth |
| GET | `/auth/google/callback` | Google OAuth callback |
| GET | `/auth/outlook` | Start Outlook OAuth |
| GET | `/auth/outlook/callback` | Outlook OAuth callback |
| POST | `/api/calendar/invite/draft` | AI-assisted invite/email draft |
| POST | `/api/calendar/invite/create` | Create calendar event |

### AI And Agents

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/agents/ask` | Ask Numa/page agent |
| POST | `/api/agents/action` | Execute limited agent actions |
| POST | `/api/ai/clientEmail/shortlist` | Generate shortlist email |
| POST | `/api/aiChat/scheduleChat` | Create candidate chat |
| GET | `/api/aiChat/getChat/{urlcode}` | Load candidate chat |
| POST | `/api/aiChat/sendChat` | Continue chat |
| POST | `/api/aiChat/sendChat/{questionNumber}` | Continue deterministic numbered chat |
| POST | `/api/aiChat/saveProgress` | Save partial chat progress |

Router prefixes are set in the route files. For example, Azure profile endpoints are exposed by included routers rather than all being directly declared in `main.py`.

## AI Capabilities

### Resume Profile Extraction

Files:

- `backend/resume_ai_profile.py`
- `backend/resume_ingest.py`
- `backend/deterministic_profile.py`
- `backend/azureUtils/routes/azureEndpoints.py`

Behavior:

- Extracts text from PDF/DOCX/TXT uploads.
- Uses deterministic parsing as a fallback.
- If `OPENAI_API_KEY` is present, `resume_ai_profile.normalize_ai_resume_profile` uses OpenAI to extract:
  - Contact.
  - Headline.
  - Summary.
  - Skills and years.
  - Culture/domain experience.
  - Portfolio/work history rows.
- Portfolio rows are written into Azure tables such as `professionalexperience`, `portfolioskill`, and `portfoliofeature`.

Model setting:

```text
OPENAI_MODEL
```

Default in code:

```text
gpt-4o-mini
```

### Job Description Processing

Files:

- `backend/jd_match.py`
- `backend/openAI/jobProcessing.py`
- `backend/azureUtils/storage/jobs.py`
- `backend/azureUtils/routes/azureJobEndpoints.py`

Behavior:

- Normalizes job descriptions into skill groups.
- Writes jobs to `jobdescription`.
- Writes required skills to `jobskills`.
- Uses `openAI.jobProcessing.processPersonalities` to infer personality fit rows in `jobpersonalities`.
- Matching uses skills from JD and candidate profile tables.

### Candidate Chat / Personality Survey

Files:

- `backend/openAI/candidateChat.py`
- `backend/openAI/engineeringSurvey.py`
- `backend/azureUtils/storage/chatLogs.py`
- `backend/azureUtils/routes/aiChatEndpoints.py`

Behavior:

- Candidate chat is deterministic by question number.
- Progress can be saved before completion.
- Chat output updates profile personality/culture completion state.
- Completion matters for onboarding eligibility.

### Numa Page Agents

Files:

- `backend/openAI/pageAgents.py`
- `backend/ui/pages/JS/pageAgents.js`
- `backend/main.py` agent endpoints.

Numa is page-aware. Built-in agent keys include:

| Agent Key | Page Focus |
| --- | --- |
| `talent` | Candidate intake |
| `match` | Internal matching |
| `external` | External sourcing |
| `profile` | Profile review and completion |
| `jobs` | Job descriptions |
| `crm` | CRM |
| `meet` | Meetings |
| `schedule` | Interviews |
| `clientcomms` | Client communication |
| `time` | Time |
| `challenge` | Test challenge |
| `cert` | AI certification |
| `badges` | Badge catalog |
| `admin` | Admin |

Important policy:

- Numa should not expose sensitive financial details unless admin/super-user access permits it.
- Numa should not claim destructive or database-changing actions occurred unless the app confirms the change.
- Numa uses app context such as domain, page, candidate, job, shortlist count, and current URL.

### Calendar Draft AI

File:

- `backend/calendar_router.py`

Behavior:

- Uses OpenAI to draft interview invitation text.
- Expects strict JSON schema in `DRAFT_SCHEMA`.
- Creates Google or Outlook events after OAuth/token checks.

### CRM News Scan

File:

- `backend/main.py`, function `_fetch_customer_news` and route `/api/crm/news-scan`.

Behavior:

- Searches Google News RSS for customer updates.
- Optionally summarizes using OpenAI if configured.
- Returns customer update signals for CRM touches.

### Client Shortlist Email

Files:

- `backend/openAI/routes/aiEndpoints.py`
- `backend/openAI/emailProcessing.py`
- `backend/ui/pages/client-comm.html`

Behavior:

- Generates client-ready shortlist email text from selected job and candidate score context.

## Storage Architecture

VETCODE uses three storage patterns:

1. Azure PostgreSQL for core candidate/profile/job data.
2. SQLite fallback files for local profile/JD storage.
3. JSON stores for workflow and operational app data.

### Azure PostgreSQL

Connection code:

`backend/azureUtils/storage/client.py`

Required environment variables:

| Variable | Purpose |
| --- | --- |
| `AZURE_DATABASE_HOST` | PostgreSQL host |
| `AZURE_DATABASE_PORT` | PostgreSQL port |
| `AZURE_DATABASE_NAME` | Database name; dev is `devready` |
| `AZURE_DATABASE_USER` | Database user |
| `AZURE_DATABASE_PASSWORD` | Database password |

Connection uses:

```python
sslmode="require"
```

Core storage files:

| File | Purpose |
| --- | --- |
| `azureUtils/storage/candidates.py` | Candidate/profile reads and writes |
| `azureUtils/storage/jobs.py` | Job description reads and writes |
| `azureUtils/storage/chatLogs.py` | AI chat and survey persistence |
| `azureUtils/storage/resumes.py` | Azure Blob resume storage |

### Local SQLite

Local database paths:

| Domain | Env Var | Default File |
| --- | --- | --- |
| Technology | `DEVREADY_DB_PATH` | `backend/devready.db` |
| Engineering | `BUILDREADY_DB_PATH` | `backend/buildready.db` |
| Law | `LEGALREADY_DB_PATH` | `backend/legalready.db` |

SQLite initialization:

`backend/storage.py:init_db`

SQLite schema:

```sql
CREATE TABLE IF NOT EXISTS profiles (
  profile_id TEXT PRIMARY KEY,
  domain TEXT,
  full_name TEXT,
  email TEXT,
  created_at TEXT,
  updated_at TEXT,
  data_json TEXT
);

CREATE TABLE IF NOT EXISTS jds (
  jd_id TEXT PRIMARY KEY,
  domain TEXT,
  company TEXT,
  title TEXT,
  created_at TEXT,
  updated_at TEXT,
  jd_text TEXT,
  jd_skills_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_profiles_domain ON profiles(domain);
CREATE INDEX IF NOT EXISTS idx_profiles_email ON profiles(email);
CREATE INDEX IF NOT EXISTS idx_jds_domain ON jds(domain);
CREATE INDEX IF NOT EXISTS idx_jds_created ON jds(created_at);
CREATE INDEX IF NOT EXISTS idx_jds_updated ON jds(updated_at);
```

### JSON Stores

Runtime JSON data lives in `backend/data/`. Deployable demo fixtures live in `data/demo_lifecycle_fixtures/`.

Relevant constants in `backend/main.py`:

| Constant | Store |
| --- | --- |
| `ACCESS_USERS_PATH` | User/access records |
| `ACCESS_CANDIDATES_PATH` | Candidate access block/delete records |
| `PROFILE_BADGES_PATH` | Badge/certification state |
| `WORKFLOW_EVENTS_PATH` | Workflow event timeline |
| `INTERVIEW_ARCHIVE_PATH` | Interview archive records |
| `MEETING_RECORDS_PATH` | Meeting output records |
| `ONBOARDING_RECORDS_PATH` | Onboarding records |
| `TIME_ENTRIES_PATH` | Time-entry records |
| `CRM_RECORDS_PATH` | CRM team/contact/deal records |
| `ACCOUNTING_RECORDS_PATH` | Accounting resources, expenses, invoices |

Important helper:

`_read_json_store_with_demo(path, fallback)`

This merges runtime JSON data with deployable demo fixtures. Runtime records override demo records by id/token when keys match.

## Azure PostgreSQL Schema Snapshot

The following schema was read from the dev PostgreSQL `public` schema on 2026-05-21. It does not include credentials.

### Table List

```text
__EFMigrationsHistory
additionaldata
address
aichatlogs
aipersonalityassessment
category
contract
country
digitalfile
emergencycontact
jobdescription
jobpersonalities
jobskills
mailingaddress
notfoundskills
person
personality
personalitygroup
phonecontact
platformactivity
platformactivityoverride
platformuser
portfoliocategory
portfoliofeature
portfolioskill
professional
professionalcategory
professionalculturalexperience
professionalexperience
professionalfeature
professionalpersonality
professionalprofile
professionalskill
professionalsurvey
professionalsurveyquestion
programminglanguage
publicprofessionalprofile
question
resumeskill
salaryrequirement
screeninginfo
skill
socialmedia
socialmediaperson
survey
surveyquestion
techcategory
technicalindicator
technicalindicatorscore
techskill
vettinginfo
```

### Core Candidate/Profile Tables

| Table | Key Columns / Purpose |
| --- | --- |
| `person` | `id`, `firstname`, `middlename`, `goesbyname`, `urlimage`, `citizenship`, `lastname`, `birthday`, `leadsource`, `updatedurlimage`, `deletedat`, `domain` |
| `professional` | `id`, `maindescription`, `personid`, `creationdate`, `modifieddate`, `url`, `status`, `email`, `linkedinurl`, `title`, `hubspotcontactid`, `hubspotdeveloperid`, `referredby` |
| `professionalprofile` | `id`, `professionalid`, `deletedat` |
| `address` | `id`, `city`, `country`, `latitude`, `longitude`, `timezone`, `personid`, `state`, `location`, `placeid` |
| `professionalexperience` | `id`, `profileid`, `description`, `mainrole`, `workexperience`, `companyname`, `startdate`, `finishdate`, `ispresent` |
| `professionalskill` | `id`, `profileid`, `years`, `skillid`, `type` |
| `resumeskill` | `id`, `profileid`, `skillid` |
| `skill` | `id`, `title`, `description`, `type`, `active`, `deletedat` |
| `professionalculturalexperience` | `id`, `profileid`, `level`, `title` |
| `professionalfeature` | `id`, `profileid`, `level`, `title` |
| `portfoliofeature` | `professionalexperienceid`, `id`, `title` |
| `portfolioskill` | `professionalexperienceid`, `skillid` |
| `portfoliocategory` | `professionalexperienceid`, `categoryid` |
| `professionalcategory` | `profileid`, `categoryid` |
| `techskill` | `id`, `profileid`, `level`, `skillid`, `type` |
| `techcategory` | `profileid`, `categoryid` |
| `publicprofessionalprofile` | `id`, `slug`, `profiledata`, `deletedat` |

### Job Tables

| Table | Columns / Purpose |
| --- | --- |
| `jobdescription` | `id`, `domain`, `company`, `jobtitle`, `createdat`, `updatedat`, `description` |
| `jobskills` | `id`, `jobid`, `skillid`, `years` |
| `jobpersonalities` | `id`, `personalityid`, `jobid`, `score` |

### Personality, Survey, Chat

| Table | Columns / Purpose |
| --- | --- |
| `aichatlogs` | `id`, `personid`, `enddate`, `completed`, `transcript`, `urlcode` |
| `aipersonalityassessment` | `id`, `personid`, `independent`, `collaborative`, `trailblazer`, `conservative`, `generalist`, `specialist`, `planner`, `doer`, `idealist`, `pragmatist`, `abstraction`, `control` |
| `personality` | `id`, `title`, `description` |
| `personalitygroup` | `id`, `leftpersonalityid`, `rightpersonalityid`, `sequence` |
| `professionalpersonality` | `id`, `profileid`, `personalityid`, `percentvalue` |
| `professionalsurvey` | `id`, `profileid`, `token` |
| `professionalsurveyquestion` | `id`, `surveyquestionid`, `professionalsurveyid`, `answer` |
| `survey` | `id`, `title` |
| `surveyquestion` | `id`, `surveyid`, `questionid` |
| `question` | `id`, `description`, `personalityid` |

### Administrative / Vetting Tables

| Table | Columns / Purpose |
| --- | --- |
| `platformactivity` | `id`, `profileid`, `step`, `notes`, `result`, `date`, `platformuserid`, `username`, `userrole`, `creationdate` |
| `platformactivityoverride` | `id`, `profileid`, `pipeline`, `reason`, `result`, `date`, `platformuserid` |
| `platformuser` | `id`, `userid`, `active`, `name` |
| `vettinginfo` | `id`, `profileid`, `technology`, `role`, `maturesoftwaredevelopmentteamexperience`, `leadershipexperienceyears`, `leadershipexperienceteamsize`, `appropriatepersonalityprofiletype`, `programminglanguageid`, `communicationproficiency`, `communicationintelligibility`, `professionallevel`, `professionaltitle`, `leadexperience`, `roledifferentiator`, `technicalcodeinterviewrating`, `technicalcodeinterviewnotes`, `extranotes`, `responsibleid`, `date`, `roletype` |
| `screeninginfo` | `id`, `profileid`, `rejectedreasonnotes`, `communicationproficiency`, `communicationintelligibility`, `generalnote`, `responsibleid`, `date`, `rejectedreason` |
| `notfoundskills` | `id`, `profileid`, `platformuserid`, `type`, `value`, `date` |

### Contact, Contract, Compensation, File Tables

| Table | Columns / Purpose |
| --- | --- |
| `phonecontact` | `id`, `personid`, `type`, `countrycode`, `number`, `numberiswhatsapp`, `sameaspersonalphonenumber` |
| `socialmedia` | `id`, `title`, `description` |
| `socialmediaperson` | `id`, `personid`, `socialmediaid`, `url` |
| `mailingaddress` | `id`, `personid`, `type`, `addressline1`, `addressline2`, `zipcode`, `companyname`, `sameaspersonalmailingaddress`, `city`, `countryid`, `stateprovince`, `countryname` |
| `emergencycontact` | `id`, `personid`, `name`, `relationship`, `countrycode`, `number` |
| `contract` | `id`, `issameaspersonalprofile`, `legalfirstname`, `legalmiddlename`, `legallastname`, `professionalid` |
| `salaryrequirement` | `id`, `professionalid`, `desiredhourlyrate`, `minimumicarate`, `projecthourlyrate`, `desiredannualsalary`, `minimumannualsalary` |
| `digitalfile` | `id`, `profileid`, `type`, `name`, `link`, `creationdate`, `size`, `deletedat` |
| `additionaldata` | `id`, `professionalid`, `employernoticerequirement`, `commutepreference`, `availability`, `preferredcommunication` |

### Reference Tables

| Table | Columns / Purpose |
| --- | --- |
| `category` | `id`, `title`, `description`, `iconreference`, `active`, `deletedat` |
| `country` | `id`, `name` |
| `programminglanguage` | `id`, `title` |
| `technicalindicator` | `id`, `description`, `deletedat` |
| `technicalindicatorscore` | `id`, `technicalindicatorid`, `vettinginfoid`, `score` |
| `__EFMigrationsHistory` | `MigrationId`, `ProductVersion` |

### Important Relationships

Common profile relationship chain:

```text
person.id
  -> professional.personid
  -> professionalprofile.professionalid
  -> professionalprofile.id
  -> professionalskill.profileid
  -> professionalexperience.profileid
  -> professionalculturalexperience.profileid
  -> professionalfeature.profileid
  -> professionalsurvey.profileid
```

Job relationship chain:

```text
jobdescription.id
  -> jobskills.jobid
  -> skill.id
  -> jobpersonalities.jobid
  -> personality.id
```

Portfolio relationship chain:

```text
professionalprofile.id
  -> professionalexperience.profileid
  -> portfolioskill.professionalexperienceid
  -> portfoliofeature.professionalexperienceid
```

## JSON Store Schemas

JSON stores are flexible, but the current app expects these shapes.

### `onboarding_records.json`

Top-level object keyed by token.

Important fields:

- `token`
- `profile_id`
- `candidate_name`
- `email`
- `title`
- `domain`
- `start_day`
- `status`
- `profile_completion`
- `profile_source`
- `source_record`
- `recipient`
- `recipient_email`
- `created_at`
- `updated_at`

### `time_entries.json`

Array of time entry rows.

Important fields:

- `id`
- `token`
- `profile_id`
- `candidate_name`
- `email`
- `domain`
- `week_start`
- `days`
- `total_hours`
- `status`
- `processed_by`
- `payment_batch`
- `processing_note`
- `client`
- `project`

### `accounting_records.json`

Object with arrays:

- `resources`
- `expenses`
- `invoices`

Resource fields:

- `id`
- `domain`
- `name`
- `email`
- `profile_id`
- `token`
- `role`
- `client`
- `bill_rate`
- `cost_rate`
- `start_date`
- `status`

Invoice fields:

- `id`
- `domain`
- `client`
- `client_email`
- `client_address`
- `invoice_number`
- `invoice_date`
- `due_date`
- `period_start`
- `period_end`
- `status`
- `payment_terms`
- `po_number`
- `line_items`
- `subtotal`
- `tax`
- `total`
- `sent_at`
- `viewed_at`

### `crm_records.json`

Array of CRM records.

Important fields:

- `id`
- `domain`
- `customer`
- `contact`
- `email`
- `phone`
- `billing_email`
- `billing_address`
- `owner`
- `value`
- `strength`
- `where`
- `when`
- `lastTouched`
- `what`
- `why`
- `history`
- `nextStep`
- `teamMembers`
- `contacts`

`teamMembers` should contain real people at the customer, not generic entities.

### Other JSON Stores

| Store | Purpose |
| --- | --- |
| `workflow_events.json` | Timeline of workflow actions |
| `interview_archive.json` | Interview and scheduling records |
| `meeting_records.json` | Meeting outputs and saved notes |
| `profile_badges.json` | Badge/certification status by profile |
| `access_users.json` | Internal/candidate/admin access records |
| `access_candidates.json` | Candidate access block/delete status |

## Environment Variables

### Core

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Enables OpenAI extraction, agents, summaries, drafts |
| `OPENAI_MODEL` | Model for resume extraction; defaults to `gpt-4o-mini` in code |
| `AZURE_DATABASE_HOST` | Azure PostgreSQL host |
| `AZURE_DATABASE_PORT` | Azure PostgreSQL port |
| `AZURE_DATABASE_NAME` | Azure PostgreSQL database name |
| `AZURE_DATABASE_USER` | Azure PostgreSQL user |
| `AZURE_DATABASE_PASSWORD` | Azure PostgreSQL password |

### Azure Blob Storage

Used by `backend/azureUtils/storage/resumes.py`.

Typical variables:

- Azure Storage connection string variable used by storage module.
- Container names for resume/file storage.

Confirm exact variable names in `backend/azureUtils/storage/resumes.py` before deployment.

### Calendar

Google:

- `GOOGLE_CLIENT_SECRET_JSON`
- `GOOGLE_CLIENT_SECRET_FILE`

Outlook:

- `OUTLOOK_CLIENT_ID`
- `OUTLOOK_CLIENT_SECRET`
- `OUTLOOK_TENANT_ID`
- Redirect URI settings if configured.

### App/HR Defaults

| Variable | Purpose |
| --- | --- |
| `HEIDI_NAME` | Default onboarding recipient name |
| `HEIDI_EMAIL` | Default onboarding recipient email |

## Data Flow By Feature

### Resume Upload To Profile

1. UI uploads resume.
2. Backend extracts text.
3. AI extraction runs if OpenAI key exists.
4. Deterministic fallback fills missing pieces.
5. Azure profile tables are written:
   - `person`
   - `professional`
   - `professionalprofile`
   - `professionalskill`
   - `resumeskill`
   - `professionalexperience`
   - `portfolioskill`
   - `portfoliofeature`
   - `professionalculturalexperience`
6. UI shows profile preview.

### JD Upload To Matching

1. UI uploads or pastes job description.
2. Backend extracts text.
3. `normalize_jd` identifies skills.
4. Azure job tables are written:
   - `jobdescription`
   - `jobskills`
   - `jobpersonalities`
5. Matching compares job skills with profile skills.
6. UI ranks candidates and exposes gaps.

### Candidate To Onboarding

1. User selects completed profile.
2. `/api/onboarding/start` verifies:
   - regular profile exists,
   - personality survey exists,
   - culture profile exists,
   - domain matches.
3. Creates or updates onboarding token in JSON store.
4. Returns:
   - `/ui/pages/onboarding.html?token=...`
   - `/ui/pages/time-entry.html?token=...`

### Time To Invoice

1. Candidate submits time through tokenized time-entry link.
2. Time Admin reviews and approves rows.
3. Accounting resource must exist with bill rate/cost rate.
4. Invoice workbench loads approved billable rows.
5. Invoice is saved to accounting JSON store.
6. Reports and invoice status use that invoice data.

### CRM To Invoice

1. CRM customer records supply customer name, AP email, billing address, contacts.
2. Accounting resources link consultants to CRM customer.
3. Invoices require CRM customer selection.
4. Reports link invoice rows back to CRM and customer status.

## Deployment Workflow

Recommended:

```powershell
cd C:\Users\darri\Documents\GitHub\VETCODE
git status
git add <changed files>
git commit -m "Describe change"
git push origin Development
railway status
railway up
```

Confirm dev:

```powershell
Invoke-RestMethod "https://vetcode-dev.up.railway.app/api/environment"
```

Expected:

- `environment`: `Development`
- `railway_environment`: `dev`
- `public_domain`: `vetcode-dev.up.railway.app`

## Local Development Checklist

1. Activate backend venv.
2. Confirm `.env` exists under `backend/`.
3. Confirm Azure DB variables are present if using real dev DB.
4. Start Uvicorn.
5. Open `http://127.0.0.1:8000/ui/pages/find-candidate.html`.
6. Check `/api/environment`.
7. Test one page in each domain.

Useful commands:

```powershell
python -m py_compile backend\main.py backend\calendar_router.py
node --check backend\ui\pages\.tmp_some_page.js
git status --short
railway logs --deployment --latest --lines 80
```

## Security And Safety Notes

- Do not commit `.env`.
- Do not print or document passwords, OAuth secrets, storage connection strings, or API keys.
- Admin and agent features must not expose financial details to non-admin users.
- Candidate onboarding must require completed profiles.
- Domain isolation is a business rule, not just a UI preference.
- External temporary profiles should not become permanent without review.
- Blank email handling is important because Azure has unique email constraints in some profile paths.
- Destructive operations should require confirmation.

## Known Technical Risks

| Risk | Area | Mitigation |
| --- | --- | --- |
| Mixed storage patterns | Azure PostgreSQL, SQLite, JSON | Keep docs current and avoid duplicating data models unnecessarily |
| Domain drift | Session storage and query params | Always read/write `domain` carefully and refresh domain-scoped data on switch |
| Empty remote JDs | Mobile/Railway dev | Mobile includes starter role fallback; full app should seed/save real JDs |
| OAuth missing locally | Calendar | Validate Google/Outlook env variables before testing |
| AI fallback differences | Resume/JD/chat | Keep deterministic fallback paths working |
| JSON runtime files ignored by Git | `backend/data` | Put deployable seed records in `data/demo_lifecycle_fixtures` |

## Current Mobile Implementation

Files:

- `backend/ui/mobile.html`
- `backend/ui/pages/mobile.html`

Purpose:

- Simple mobile-first candidate pick and onboarding flow.
- Does not replace desktop app.

Flow:

1. Select domain.
2. Select saved job or starter role.
3. Select completed profile.
4. Create onboarding link.
5. Copy onboarding and time-entry links.

API dependencies:

- `/api/jd/list`
- `/api/onboarding/candidates`
- `/api/onboarding/admin`
- `/api/onboarding/start`

## How To Extend Safely

When adding a new feature:

1. Decide the domain behavior first.
2. Decide which storage layer owns the data.
3. Add or reuse backend endpoint.
4. Add UI with domain-specific styling.
5. Add helpful empty states and next-step links.
6. Add deployable demo fixture if the feature needs demo data.
7. Test local and Railway dev.
8. Update this guide and the user guide.

When adding database tables:

1. Prefer existing Azure schema if the concept already belongs to candidate/job/profile.
2. Use JSON store only for lightweight operational records or prototype workflows.
3. If JSON data becomes core business data, plan migration to PostgreSQL.
4. Add schema notes here.


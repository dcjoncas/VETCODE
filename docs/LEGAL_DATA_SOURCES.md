# Legal Data Source Setup

VETCODE LegalReady uses separate providers for professional discovery, public-web research, and court-record evidence. Keys are read only by the FastAPI backend and are never returned to the browser.

## Provider roles

| Provider | VETCODE role | Environment variable | Current entry option |
| --- | --- | --- | --- |
| Coresignal | Secondary professional-profile discovery and PDL comparison | `CORESIGNAL_API_KEY` | 7-day free trial, no credit card, 200 collect and 400 search credits |
| Brave Search API | Transient law-firm bio, practice-page, publication, and public evidence search | `BRAVE_SEARCH_API_KEY` | $5 in free Search credits each month; account and payment verification required |
| CourtListener / RECAP | Manual docket and published-opinion evidence review | `COURTLISTENER_API_TOKEN` | CourtListener account token with low default limits; membership or commercial access increases limits |

Access links:

- Coresignal signup: https://dashboard.coresignal.com/sign-up
- Brave Search plans: https://brave.com/search/api/
- Brave API keys: https://api-dashboard.search.brave.com/app/keys
- CourtListener account: https://www.courtlistener.com/sign-in/
- CourtListener API token: https://www.courtlistener.com/profile/api-token/
- Free Law Project membership: https://free.law/membership/

## Configuration

For local development, place account-owned values in `.env`. Never commit `.env` or paste keys into source code.

For Railway, add the three variables in the `VETCODE / dev / VETCODE` service Variables panel. Railway should redeploy after the variables are saved.

Verify readiness without exposing secrets:

```text
GET /api/azureJobs/external/providers
```

Each provider returns only `ready: true|false`, its role, and its official setup link.

## Usage and safeguards

- Coresignal uses the Base Employee Search Preview endpoint. Each successful preview page consumes one search credit. VETCODE does not collect full profiles automatically.
- Brave uses Web Search with LinkedIn excluded from the query. Results remain transient and cannot be imported as TEMP candidate profiles unless a future Brave plan explicitly grants storage rights.
- CourtListener performs two explicit searches per evidence check: one RECAP docket search and one published-opinion search.
- CourtListener name matches never change a candidate score, verify identity, prove an appearance, or replace California Bar verification.
- No provider connector requests personal email, phone, or street-address data.
- Professional-network URLs may be displayed when a licensed provider returns them, but VETCODE does not scrape LinkedIn.

## Validation

Run the focused suite from `backend`:

```powershell
python -m unittest discover -s tests -v
```

After deploying keys, make one low-volume request per provider and review its source audit before enabling broader use.

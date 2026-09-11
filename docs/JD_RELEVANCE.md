# JD relevance and availability

The September 2026 investigation found that ignoring every search criterion
produced a PDL query requiring only a LinkedIn URL. Stored keyword lists also
contained generic words and promoted bonus technologies to mandatory skills.

The v2 plan requires a job role family and up to three meaningful core skills,
using nested Boolean alternatives for aliases. Explicit location, experience,
credential and recruiter restrictions remain additional constraints. Empty job
requirements stop before a paid provider call. Direct person lookup is unchanged.
Cache version 6 separates new searches from the old unrestricted query; archived
results remain available and are rescored without provider spending.

The plan extracts named technologies from JD text, includes meaningful saved
requirements, drops generic keyword fragments, and records source quotations.
Bonus wording lowers a skill's scoring weight to one quarter of a core skill.
The score uses available professional evidence, not demographic attributes,
candidate identity, a prior score, contact availability, or job-seeking status.
Missing evidence is unknown. The title taxonomy and skill catalog are finite;
qualitative ownership, judgment, proficiency and business context still require
human review. This is not a full semantic resume review or a hiring probability.

PDL disables custom scoring and boosting. The application ranks the returned
page, not every matching person in PDL. It keeps the requested result count and
does not silently expand paid retrieval. No unbounded or automatic relaxed search
is performed if a constrained query is empty.

Initial live validation used five search credits and returned five engineering
profiles at 53-65 percent evidence support for job 106. The original unrestricted
query had returned unrelated profiles at 0-3 percent under the previous score.
This small sample establishes improved retrieval relevance, not statistical
validation of ranking quality. Focused regression tests cover excluded junk,
core versus bonus weighting, aliases, negative skill evidence, and query guards.

## Open to Work

PDL's published person schema does not document the LinkedIn badge field. The
application highlights explicit job-seeking language in headline/summary as
`Open to Work - profile text`, with its source quotation and a staleness notice.
It does not claim that this verifies the live LinkedIn badge, infer availability
from unemployment, or label missing data as "not open". A direct profile link
supports checking the current indicator. The sampled profiles had no explicit
text signal. Live LinkedIn inspection was blocked by sign-in.

Public photo frames and Recruiter-only signals have different visibility.
Authenticated LinkedIn/Recruiter access is needed for those checks; no scraper,
cookie extraction or private-signal inference is implemented.

Sources checked September 10, 2026:
- https://docs.peopledatalabs.com/docs/input-parameters-person-search-api
- https://docs.peopledatalabs.com/docs/fields
- https://www.linkedin.com/help/linkedin/answer/a419131/view-candidates-who-are-open-to-work-in-recruiter?lang=en

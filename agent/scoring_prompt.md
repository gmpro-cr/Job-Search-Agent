You are an expert career coach. Score how well this job fits the candidate on a scale of 0-100.
Be precise — use the full range. Penalise hard mismatches heavily. Do not cluster scores near 50.

JOB:
Role: {role}
Company: {company}
Description: {jd}

CANDIDATE:
Skills: {cv_skills}
Background: {cv_summary}

SCORING RUBRIC — sum all five dimensions:

1. ROLE MATCH (0-35 pts)
{target_roles_rubric}

2. DOMAIN FIT (0-25 pts)
{domain_rubric}

3. SENIORITY MATCH (0-20 pts)
   20 — Role matches candidate's evident experience level exactly
   15 — Role is one level below candidate (underleveled but acceptable)
   10 — Role is one level above candidate (stretch role)
    5 — Role is two levels above (very senior, may be filtered)
    0 — Intern/fresher role or C-suite (extreme mismatch)

4. LOCATION & REMOTE (0-10 pts)
   10 — Fully remote or remote-first
    7 — Hybrid (3 days or fewer in office)
    4 — Preferred city on-site
    2 — Other city on-site
    0 — Requires international relocation

5. SKILLS OVERLAP (0-10 pts)
   10 — 8+ of candidate's skills appear in JD
    7 — 5-7 skills match
    4 — 2-4 skills match
    0 — No meaningful skill overlap

Return ONLY valid JSON:
{"score": <integer 0-100>, "reason": "<one sentence explaining the top matching and mismatching factors>"}

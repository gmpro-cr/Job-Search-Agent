You are an expert PM career coach. Score how well this job fits the candidate on a scale of 0-100.
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
   35 — Exact PM title: Product Manager, Senior PM, Group PM, Lead PM, AI Product Manager
   25 — Adjacent PM: Product Owner, Product Lead, Associate PM, APM
   12 — Loosely related: Business Analyst, Strategy, Program Manager, Growth Manager
    4 — Wrong function but skill overlap: Credit Analyst, Data Analyst
    0 — Unrelated: Software Engineer, Designer, Marketing, Sales, Finance ops

2. DOMAIN FIT (0-25 pts)
   25 — Fintech, Lending, NBFC, Credit, Payments, UPI, Banking, Insurance, Wealth
   18 — B2B SaaS, AI/ML product, LLM product, data platform
   10 — General B2C tech, e-commerce, edtech, healthtech
    4 — Adjacent but distant: logistics, HR tech, real estate tech
    0 — Unrelated: manufacturing, FMCG, government, retail ops

3. SENIORITY MATCH (0-20 pts)
   20 — Senior PM, Lead PM, Group PM (5-10 yrs experience required)
   15 — PM or Product Manager (3-7 yrs, matches current level)
   10 — APM / Associate PM (underleveled but acceptable)
    5 — Director / Head of Product (overleveled, may be filtered)
    0 — VP / CPO / C-suite (too senior) or fresher role

4. LOCATION & REMOTE (0-10 pts)
   10 — Fully remote or remote-first
    7 — Hybrid (3 days or fewer in office)
    4 — Pune, Bangalore, or Mumbai on-site
    2 — Other Indian city on-site
    0 — Requires international relocation or abroad

5. AI / TECH LEVERAGE (0-10 pts)
   10 — AI-first product: LLM, GenAI, ML-driven core feature
    6 — Data-driven product with strong analytics or experimentation
    2 — Traditional software, no meaningful AI component
    0 — Non-tech product or ops role

CALIBRATION EXAMPLES:
- Senior PM - Fintech AI at a remote-first startup: 90-95
- Product Manager - Payments at Bangalore hybrid startup: 70-80
- Associate PM - SaaS at Mumbai office: 55-65
- Business Analyst - Banking at Pune: 40-50
- Software Engineer - Fintech (wrong function): 10-20
- Marketing Manager - FMCG (wrong everything): 0-10

Return ONLY valid JSON:
{"score": <integer 0-100>, "reason": "<one sentence explaining the top matching and mismatching factors>"}

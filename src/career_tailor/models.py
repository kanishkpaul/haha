"""Data shapes passed between stages.

To define:
- Claim: one atomic, citable fact (id, wording variants, metrics with their
  qualifying conditions, skills, dates, team credit, version status, visibility).
- Entry: a role, project, award or education item grouping claims.
- JobSpec: title, seniority, weighted must-have and nice-to-have requirements,
  knockout criteria, canonical keywords with synonyms, ATS vendor.
- Coverage: requirement -> supporting claim ids with strong/partial/none.
- Selection: chosen entries and claims with the reason each was picked.
- TailoredBullet: final text plus the claim ids it rests on.
"""

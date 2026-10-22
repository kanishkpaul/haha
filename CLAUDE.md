# career-tailor

Tailors Kanishk Paul's resume to one job. See `README.md` for layout and stages.

## Sources of truth

- Facts come only from `../outputs/KANISHK_PAUL_MASTER_CAREER_RECORD.md`. Never add a skill, metric, title, date, employer or outcome it does not contain.
- Keep each metric's qualifying conditions (record section 15) and the attribution, version and visibility rules (section 12.3). Team results stay team results; don't link private repos.
- Write at the record's "strongest reasonable" ceiling: ownership, depth, scale, outcomes.

## Resume-Matcher

- Vendored as a submodule at `vendor/resume-matcher`; do not edit it. Run its tools with `bin/rm <tool>` and see `.claude/skills/resume-matcher/SKILL.md`.
- It rephrases but never selects: tailoring keeps every entry and bullet of its source master. Selection happens in this repo first, and masters must already be close to page size.
- At most five masters, one per role track (record section 12.1).

## Conventions

- Python 3.13, managed with `uv`. Package code in `src/career_tailor/`, one module per stage.
- Personal inputs and outputs live in `jobs/` and `applications/`, which are gitignored.

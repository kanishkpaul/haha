# haha (career-tailor)

Tailors Kanishk Paul's resume to one job. `README.md` is the overview; `docs/workflow.md` is the working guide.

## Doing an application

- `uv run tailor run <url | job file | folder>` runs every stage it can. `uv run tailor status` lists every application, its stage and the next command.
- When the run stops for a prompt pack (`work/jobspec.prompt.md`, `work/match.prompt.md`, `work/write.prompt.md`, `work/verify.prompt.md`), you fill the JSON file it names (`jobspec.json`, `match.json`, `draft.json`, `review.json`), then rerun `tailor run`. Follow the pack's rules; the validators reject paraphrased quotes, uncited facts and missing conditions.
- Gates 1 (plan) and 2 (final page) belong to Kanishk. Approve one only when he asks or has said so for that job.

## Sources of truth

- Facts come only from `../outputs/KANISHK_PAUL_MASTER_CAREER_RECORD.md`. Never add a skill, metric, title, date, employer or outcome it does not contain.
- Every record entry is true. Weak or missing proof means a lost artifact, never doubt; proof strength must not decide what goes on the page.
- Keep each metric's qualifying conditions (record section 15) and the attribution, version and visibility rules (section 12.3). Team results stay team results; don't link private repos.
- Write at the record's "strongest reasonable" ceiling: ownership, depth, scale, outcomes. Use the posting's exact terms wherever a claim describes that work.
- Corrections go in `../haha-data/record_updates.yaml`, not in the record: `../evidence/` pins the record's SHA-256 and line numbers. Detail from the old megadoc (section 17) enters through `archive_claims` in an entry's overlay, copied word for word.

## Resume-Matcher

- Vendored as a submodule at `vendor/resume-matcher`; do not edit it. Run its tools with `bin/rm <tool>` and see `.claude/skills/resume-matcher/SKILL.md`.
- haha uses `parse_check_file` and `score_resume` with inline data; neither writes to RM's database. Don't store resumes or jobs there without asking: it holds at most five masters.

## Conventions

- Python 3.13, managed with `uv`. Package code in `src/career_tailor/`, one module per stage; `cli.py` wires the commands. Lint with `uv run ruff check`; test with `uv run pytest`.
- **No personal data in this repo.** The bank, applications, jobs and calibration live in `../haha-data/` (`HAHA_DATA_DIR`; see `paths.py`). Tests use the synthetic record in `tests/fixtures/`; tests marked `local` use the real record and Resume-Matcher, and skip when they are absent.
- Inside an application folder, files people read sit at the top; prompt packs, check logs, raw fetches and render scratch go under `work/` (`paths.WORK_DIR`).
- Build history and decisions: `docs/plans/2026-10-23-build-plan.md`.

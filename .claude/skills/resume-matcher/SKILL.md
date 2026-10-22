---
name: resume-matcher
description: Drive the local Resume-Matcher install (agent mode, no API key) to store, validate, score, parse-check and render tailored resumes. Use when tailoring a resume to a job, scoring a resume against a job description, parse-checking a PDF, or exporting a resume PDF.
argument-hint: "[tailor | score | parse-check | <job description>]"
---

# Resume-Matcher in this repo

Follow the upstream skill at `vendor/resume-matcher/.claude/skills/resume-matcher/SKILL.md` for the workflow, writing rubric and tailoring rules, with these local differences:

- **Command:** use `bin/rm <tool> [arguments]` from this repo's root instead of `uv run --directory apps/backend resume-matcher …`.
- **Setup is done.** Agent mode is on in `vendor/resume-matcher/apps/backend/.env`. If `curl -s http://127.0.0.1:3000/api/v1/health` fails, start the servers with `scripts/start-servers.sh` (or the `rm-backend` / `rm-frontend` launch configs).
- **Scratch files** go in `jobs/.work/` here (gitignored), always passed as absolute paths.
- **Masters come from this repo, not from a resume import.** The five track masters are built from the evidence bank into `data/masters/*.json` and loaded with `create_master_resume`. Never create a master from the full career record: Resume-Matcher keeps every entry and bullet of its source, so selection must happen first (see `CLAUDE.md`).
- **Facts:** the career record at `../outputs/KANISHK_PAUL_MASTER_CAREER_RECORD.md` is the source of truth. Keep each metric's qualifying conditions (section 15) and the attribution rules in section 12.3.

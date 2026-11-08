# Workflow guide

How to take one posting to a finished resume, what every file is for, and how to steer the result. The README has the short version.

## The loop

```bash
uv run tailor run <posting url | job file | application folder>
uv run tailor status
```

`tailor run` does every stage it can, then stops for one of two reasons:

1. **A prompt pack needs filling.** The thinking steps (reading the posting, judging the evidence, writing bullets, reviewing the page) are done by Claude in this repo's session, not by an API call. The run writes a prompt pack to `work/`, and Claude writes the JSON file it asks for. Run `tailor run` again and it validates that file and carries on.
2. **A gate needs you.** Gate 1 is the plan, gate 2 is the finished page. Read the files it names, then approve with `tailor run <name> --approve`. One approval passes one gate.

`<name>` can be the full folder name or any unique prefix (`anthropic`, `metr`). `tailor status` lists every application, its stage, its screener grade and ATS score, and the exact next command.

| Stage | Stops until | Who |
|---|---|---|
| job spec | `jobspec.json` exists and passes its check | Claude, from `work/jobspec.prompt.md` |
| match | `match.json` exists and passes its check | Claude, from `work/match.prompt.md` |
| plan | `selection.json` is newer than its inputs | `tailor` |
| **gate 1** | you approve the plan | you |
| write | `draft.json` exists and passes the guard | Claude, from `work/write.prompt.md` |
| review | `review.json` exists | Claude, from `work/verify.prompt.md` |
| **gate 2** | you approve the page | you |
| ready | — | send the PDF |

Postings can come from a Greenhouse, Ashby or Lever URL, a local `.md`/`.txt` file, or a file name dropped into `../haha-data/jobs/` (`tailor run acme.md`).

## The application folder

`../haha-data/applications/<company>-<role>-<date>/`

| File | What it is |
|---|---|
| `Kanishk_Paul_Resume.pdf` / `.docx` / `.txt` | The resume, a Word copy, and the text an ATS reads out of the PDF |
| `check_report.md` | Findings, render log, Resume-Matcher parse check and keyword score, screener grade |
| `gap_report.md` | Which requirements the record supports strongly, weakly or not at all |
| `plan.md` | What goes on the page, what was left out and why |
| `draft.md` | The draft as readable text |
| `knockouts.md` | Visa, location, degree and years checks against `candidate.yaml` |
| `provenance.json` | Every line on the page → its claims → their record lines → proof cards and artifacts |
| `posting.md` | The posting as fetched |
| `jobspec.json`, `match.json`, `draft.json`, `review.json` | Claude's outputs, validated by `tailor` |
| `selection.json` | The plan as data |
| `ats_score.json` | Resume-Matcher's score, sub-scores and missing posting terms |
| `approvals.json` | Gate approvals, each with a hash of what was approved |
| `plan.edits.yaml` | Your pins and bans (optional, see below) |
| `work/` | Prompt packs, check logs, the raw fetch, RenderCV input and Typst source |

## Steering

**Pin or ban.** Create `plan.edits.yaml` in the application folder:

```yaml
pin: [EXP-01.b2, ENG-01]   # entries or claims that must appear
ban: [ENG-14]              # entries or claims that must not
pages: 1                   # override the page count
dense: true                # ~9 pt type, 0.4 in margins, fuller skills block (profile resumes)
```

`tailor run` sees the change, re-plans and stops at gate 1 again. `plan.md` shows what each pin cost.

**Correct a record fact.** Add a dated entry to `../haha-data/record_updates.yaml`; never edit the record itself, because `../evidence/` pins its hash and line numbers. Then run `uv run tailor bank`.

**Add detail from the old resume text.** An entry's overlay in `../haha-data/annotations/<ID>.yaml` can list `archive_claims`: sentences copied word for word from section 17 of the record. Each becomes a claim with its own record line. Rebuild with `uv run tailor bank`.

**Fix eligibility facts.** Edit `../haha-data/candidate.yaml` (work authorization, relocation, start date), then rerun the job's check.

## What the checks guarantee

- Every bullet cites claims from the plan, and every number, name and tool in it appears in those claims.
- A metric keeps its condition ("54/55 convulsive events, two channels, retrospective").
- Ownership words match the record: "co-led" never becomes "led".
- The PDF reads back in order with no ligature garbage, the contact details extract, and it fits the page count.
- Every requirement the plan supports strongly reads as Meets to a strict screener.

Proof strength never decides what goes on the page. Every record entry is true; a claim whose artifact was lost counts the same as one with a link. Proof stays known (proof cards, provenance) but never reaches the match, write or verify prompts, the plan or the solver. The gap report lists those claims under "No artifact to show" so you can prepare the story. `tests/test_truth_rule.py` enforces this.

## Page fit

`tailor run` renders with RenderCV/Typst at 10 pt. If the page overflows it tightens type and spacing down to 9.5 pt, then re-plans using only bullets already written. If the last page is under 70% full it loosens up to 10.5 pt. Each measured page updates `../haha-data/calibration.yaml` (one measurement per job), which later plans use to size the page.

## Messages you may see

| Message | Meaning |
|---|---|
| `warning: the career record changed since the bank was built` (or record updates, proof cards, annotations) | Run `uv run tailor bank`. |
| `gate 1 (plan) … (changed since it was approved)` | The job spec, match or plan edits changed; review the new plan. |
| `warning: the draft changed after the review` | Ask Claude to review the page again. |
| `fit: took … off the page to fit` | The fit loop dropped written bullets; plan and draft were updated. |
| `required posting term '…' isn't on the page` | An ATS search for that exact word will miss the page. Use it if a claim describes that work. |

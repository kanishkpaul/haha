"""Command-line entry point: `uv run tailor <command>`."""

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from . import annotate, bank, ingest, jobspec, knockouts, match, opportunities, paths, selection, verify, write
from .models import Posting, Review, Selection, Writing

COMMANDS = {
    "bank": "Compile the career record into the evidence bank",
    "masters": "Build and load the five role-track masters",
    "job": "Analyse a job description into a job spec",
    "plan": "Gap report and selection plan for a job",
    "write": "Write the tailored resume for an approved plan",
    "check": "Verify, render, parse-check and score an application",
}


def cmd_bank(args: argparse.Namespace) -> int:
    p = paths.load()
    if args.annotate:
        b = bank.load(p.bank)
        tracks = [t.target for t in b.tables.tracks]
        packs = annotate.write_packs(list(b.entries.values()), b.tables.skills, tracks, p.annotations,
                                     p.work / "annotate", b.proof)  # fmt: skip
        print(f"annotate: {len(packs)} prompt packs -> {p.work / 'annotate'}; write overlays to {p.annotations}")
        return 0
    if args.report:
        b = bank.load(p.bank)
        if reason := bank.stale_reason(b, p):
            print(f"warning: {reason}", file=sys.stderr)
    else:
        if not p.career_record.is_file():
            print(f"error: no career record at {p.career_record} (set HAHA_CAREER_RECORD)", file=sys.stderr)
            return 1
        b = bank.compile_bank(p)
        bank.save(b, p.bank)
        claims = b.manifest.claim_count
        print(f"bank: {b.manifest.entry_count} entries, {claims} claims -> {p.bank}")
    out = p.bank / "REPORT.md"
    out.write_text(bank.report(b), encoding="utf-8")
    problems = b.notes.get("problems", [])
    print(f"report: {out} ({len(problems)} build notes)")
    return 0


def resolve_application(p: paths.Paths, name: str) -> Path | None:
    """An application folder from a path, a folder name, or a unique name prefix."""
    direct = Path(name).expanduser()
    if (direct / "posting.json").is_file():
        return direct
    matches = sorted(f for f in p.applications.glob(f"{name}*") if (f / "posting.json").is_file())
    return matches[0] if len(matches) == 1 else None


def cmd_job(args: argparse.Namespace) -> int:
    p = paths.load()
    if args.check:
        folder = resolve_application(p, args.check)
        if folder is None:
            print(f"error: no single application folder matches {args.check!r} in {p.applications}", file=sys.stderr)
            return 1
        report, spec = jobspec.check_folder(folder)
        print(jobspec.render_check(report, None).split("\n", 2)[2].strip())
        if not report.ok or spec is None:
            return 1
        posting = Posting.model_validate_json((folder / "posting.json").read_text(encoding="utf-8"))
        candidate = knockouts.load_candidate(p.candidate)
        if candidate is None:
            print(f"warning: no {p.candidate}; knockouts not judged", file=sys.stderr)
        results = knockouts.evaluate(spec, posting, candidate) if candidate else []
        match = opportunities.find(p.outputs, [posting.url, spec.url])
        (folder / knockouts.KNOCKOUT_FILE).write_text(knockouts.render(spec, posting, results, candidate, match),
                                                      encoding="utf-8")  # fmt: skip
        counts = {s: sum(k.status == s for k in results) for s in ("blocker", "condition", "check", "pass")}
        print(
            "knockouts: " + ", ".join(f"{n} {s}" for s, n in counts.items()) + f" -> {folder / knockouts.KNOCKOUT_FILE}"
        )
        return 0
    if not args.source:
        print("error: give a posting URL or file, or --check FOLDER", file=sys.stderr)
        return 2
    source = Path(args.source).expanduser()
    try:
        posting, raw = ingest.from_file(source) if source.is_file() else ingest.from_url(args.source)
    except (ValueError, LookupError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if args.company:
        posting.company = args.company
    elif (match := opportunities.find(p.outputs, [posting.url, args.source])) and match.organization:
        posting.company = match.organization  # boards give slugs like "apolloresearch"
    folder = ingest.save_posting(p.applications, posting, raw)
    pack = jobspec.write_pack(folder)
    print(f"job: {posting.title} at {posting.company} ({posting.ats}) -> {folder}")
    print(f"next: fill {folder / jobspec.SPEC_FILE} from {pack.name}, then `tailor job --check {folder.name}`")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    p = paths.load()
    folder = resolve_application(p, args.folder)
    if folder is None:
        print(f"error: no single application folder matches {args.folder!r} in {p.applications}", file=sys.stderr)
        return 1
    spec_report, spec = jobspec.check_folder(folder)
    if not spec_report.ok or spec is None:
        print(f"error: the job spec has errors; run `tailor job --check {folder.name}` first", file=sys.stderr)
        return 1
    try:
        b = bank.load(p.bank)
    except FileNotFoundError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if reason := bank.stale_reason(b, p):
        print(f"warning: {reason}", file=sys.stderr)
    retrieval = match.retrieve(b, spec)
    pack = match.write_pack(folder, b, spec, retrieval)
    m, report = match.load_match(folder)
    if m is None and not (folder / match.MATCH_FILE).is_file():
        print(f"plan: {len(retrieval.hits)} requirements retrieved; suggested track {retrieval.tracks[0]!r}")
        print(f"next: fill {folder / match.MATCH_FILE} from {pack.name}, then `tailor plan {folder.name}`")
        return 0
    if m is not None:
        report = match.validate(m, spec, b)
    (folder / match.CHECK_FILE).write_text(match.render_check(report), encoding="utf-8")
    for f in report.findings:
        print(f"{f.severity}: [{f.check}] {f.message}")
    if not report.ok or m is None:
        return 1
    (folder / match.GAP_FILE).write_text(match.gap_report(spec, m, b, retrieval), encoding="utf-8")
    verdicts = {v: sum(c.verdict == v for c in m.coverage) for v in ("strong", "partial", "none")}
    basic = match.weighted_coverage(spec, m, "basic")
    print(
        f"gap report: {verdicts['strong']} supported, {verdicts['partial']} weak, {verdicts['none']} unsupported"
        + (f"; basic coverage {basic:.0%}" if basic is not None else "")
        + f" -> {folder / match.GAP_FILE}"
    )
    try:
        edits = selection.load_edits(folder)
        sel = selection.solve(b, spec, m, edits)
    except (ValidationError, selection.SelectionError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    (folder / selection.SELECTION_FILE).write_text(sel.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (folder / selection.PLAN_FILE).write_text(selection.render_plan(b, spec, m, sel), encoding="utf-8")
    lost = [r for r, v in sel.covered.items() if v != next(c.verdict for c in m.coverage if c.requirement_id == r)]
    print(
        f"plan: {len(sel.picks)} entries, about {sel.lines_used:g} of {sel.lines_budget:g} lines"
        + (f"; lost to the budget: {', '.join(lost)}" if lost else "; nothing matched was lost")
        + f" -> {folder / selection.PLAN_FILE}"
    )
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    p = paths.load()
    folder = resolve_application(p, args.folder)
    if folder is None:
        print(f"error: no single application folder matches {args.folder!r} in {p.applications}", file=sys.stderr)
        return 1
    spec_report, spec = jobspec.check_folder(folder)
    m, match_report = match.load_match(folder)
    sel_path = folder / selection.SELECTION_FILE
    if not spec_report.ok or spec is None or m is None or not sel_path.is_file():
        print(f"error: no approved plan yet; run `tailor plan {folder.name}` first", file=sys.stderr)
        return 1
    if (folder / match.MATCH_FILE).stat().st_mtime > sel_path.stat().st_mtime or (
        (folder / selection.EDITS_FILE).is_file()
        and (folder / selection.EDITS_FILE).stat().st_mtime > sel_path.stat().st_mtime
    ):
        print(f"warning: match.json or plan edits changed after the plan; rerun `tailor plan {folder.name}`",
              file=sys.stderr)  # fmt: skip
    b = bank.load(p.bank)
    if reason := bank.stale_reason(b, p):
        print(f"warning: {reason}", file=sys.stderr)
    sel = Selection.model_validate_json(sel_path.read_text(encoding="utf-8"))
    record_text = p.career_record.read_text(encoding="utf-8") if p.career_record.is_file() else ""
    pack = write.write_pack(folder, b, spec, m, sel, record_text)
    draft_path = folder / write.WRITING_FILE
    if not draft_path.is_file():
        print(f"write: {sum(len(pk.claim_ids) for pk in sel.picks)} claims to write")
        print(f"next: fill {draft_path} from {pack.name}, then `tailor write {folder.name}`")
        return 0
    try:
        w = Writing.model_validate_json(draft_path.read_text(encoding="utf-8"))
    except ValidationError as err:
        print(f"error: {write.WRITING_FILE}: {str(err)[:500]}", file=sys.stderr)
        return 1
    report = write.guard(w, b, spec, m, sel)
    (folder / write.CHECK_FILE).write_text(write.render_check(report), encoding="utf-8")
    for f in report.findings:
        print(f"{f.severity}: [{f.check}] {f.message}")
    if not report.ok:
        return 1
    (folder / write.DRAFT_FILE).write_text(write.render_draft(b, spec, sel, w), encoding="utf-8")
    warnings = sum(f.severity == "warning" for f in report.findings)
    print(f"draft: {len(w.bullets)} bullets, {warnings} warnings -> {folder / write.DRAFT_FILE}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    p = paths.load()
    folder = resolve_application(p, args.folder)
    if folder is None:
        print(f"error: no single application folder matches {args.folder!r} in {p.applications}", file=sys.stderr)
        return 1
    spec_report, spec = jobspec.check_folder(folder)
    m, _ = match.load_match(folder)
    sel_path, draft_path = folder / selection.SELECTION_FILE, folder / write.WRITING_FILE
    if not spec_report.ok or spec is None or m is None or not sel_path.is_file() or not draft_path.is_file():
        print(f"error: no draft yet; run `tailor write {folder.name}` first", file=sys.stderr)
        return 1
    b = bank.load(p.bank)
    if reason := bank.stale_reason(b, p):
        print(f"warning: {reason}", file=sys.stderr)
    sel = Selection.model_validate_json(sel_path.read_text(encoding="utf-8"))
    try:
        w = Writing.model_validate_json(draft_path.read_text(encoding="utf-8"))
    except ValidationError as err:
        print(f"error: {write.WRITING_FILE}: {str(err)[:500]}", file=sys.stderr)
        return 1
    checks = write.guard(w, b, spec, m, sel).findings + verify.facts(w, b, sel) + verify.lint(w, b, spec, sel)
    pack = verify.write_pack(folder, b, spec, sel, w)
    review, review_findings = None, []
    review_path = folder / verify.REVIEW_FILE
    if review_path.is_file():
        try:
            review = Review.model_validate_json(review_path.read_text(encoding="utf-8"))
        except ValidationError as err:
            print(f"error: {verify.REVIEW_FILE}: {str(err)[:500]}", file=sys.stderr)
            return 1
        if review_path.stat().st_mtime < draft_path.stat().st_mtime:
            print("warning: the draft changed after the review; review it again", file=sys.stderr)
        review_findings = verify.check_review(review, w, spec, sel, verify.page_text(b, spec, sel, w))
    report = folder / verify.REPORT_FILE
    report.write_text(verify.render_report(spec, w, checks, review, review_findings), encoding="utf-8")
    findings = checks + review_findings
    for f in findings:
        print(f"{f.severity}: [{f.check}] {f.message}")
    errors = sum(f.severity == "error" for f in findings)
    if review is None:
        print(f"check: {errors} errors so far")
        print(f"next: fill {review_path} from {pack.name}, then `tailor check {folder.name}`")
        return 1 if errors else 0
    letter = verify.grade(spec, review)[0]
    print(f"check: {errors} errors, {len(findings) - errors} warnings, screener grade {letter} -> {report}")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tailor", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        cmd = sub.add_parser(name, help=help_text)
        if name == "bank":
            cmd.add_argument("--report", action="store_true", help="rewrite REPORT.md from the saved bank only")
            cmd.add_argument(
                "--annotate", action="store_true", help="write annotation prompt packs from the saved bank"
            )
        if name == "job":
            cmd.add_argument(
                "source", nargs="?", help="posting URL (Greenhouse, Ashby, Lever) or a job-description file"
            )
            cmd.add_argument(
                "--check", metavar="FOLDER", help="validate an application's jobspec.json against its posting"
            )
            cmd.add_argument("--company", help="company name to use instead of the board's")
        if name in ("plan", "write", "check"):
            cmd.add_argument("folder", help="application folder (path, name, or unique name prefix)")
    args = parser.parse_args(argv)
    if args.command == "bank":
        raise SystemExit(cmd_bank(args))
    if args.command == "job":
        raise SystemExit(cmd_job(args))
    if args.command == "plan":
        raise SystemExit(cmd_plan(args))
    if args.command == "write":
        raise SystemExit(cmd_write(args))
    if args.command == "check":
        raise SystemExit(cmd_check(args))
    raise SystemExit(f"tailor {args.command}: not implemented yet")


if __name__ == "__main__":
    main()

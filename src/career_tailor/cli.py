"""haha: tailor one resume to one job. Start with `tailor run <posting url>`; `tailor status` says what's next."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from . import (
    annotate,
    assemble,
    ats,
    bank,
    ingest,
    jobspec,
    knockouts,
    match,
    opportunities,
    paths,
    render,
    selection,
    verify,
    write,
)
from .models import Finding, Posting, Review, Selection, Writing

COMMANDS = {
    "bank": "Compile the career record into the evidence bank",
    "job": "Analyse a job description into a job spec",
    "plan": "Gap report and selection plan for a job",
    "write": "Write the tailored resume for an approved plan",
    "check": "Verify, render, parse-check and score an application",
    "run": "Run every stage for a posting, stopping at prompt packs and the two gates",
    "status": "List applications, where each stands and what to do next",
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
    folder = ingest_posting(p, args.source, args.company)
    if folder is None:
        return 1
    print(f"next: fill {folder / jobspec.SPEC_FILE} from {jobspec.PACK_FILE}, then `tailor job --check {folder.name}`")
    return 0


def ingest_posting(p: paths.Paths, given: str, company: str | None) -> Path | None:
    """Fetch or read a posting into its application folder and write the job-spec prompt pack."""
    source = Path(given).expanduser()
    try:
        posting, raw = ingest.from_file(source) if source.is_file() else ingest.from_url(given)
    except (ValueError, LookupError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return None
    if company:
        posting.company = company
    elif (match := opportunities.find(p.outputs, [posting.url, given])) and match.organization:
        posting.company = match.organization  # boards give slugs like "apolloresearch"
    folder = ingest.save_posting(p.applications, posting, raw)
    jobspec.write_pack(folder)
    print(f"job: {posting.title} at {posting.company} ({posting.ats}) -> {folder}")
    return folder


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
    paths.prepared(folder / match.CHECK_FILE).write_text(match.render_check(report), encoding="utf-8")
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
        calibration = render.load_calibration(p.data / render.CALIBRATION_FILE)
        sel = selection.solve(b, spec, m, edits, lines_per_page=calibration.get(selection.paper_for(spec)))
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
    paths.prepared(folder / write.CHECK_FILE).write_text(write.render_check(report), encoding="utf-8")
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
    if any(f.severity == "error" for f in checks):
        (folder / verify.REPORT_FILE).write_text(verify.render_report(spec, w, checks, None, []), encoding="utf-8")
        for f in checks:
            print(f"{f.severity}: [{f.check}] {f.message}")
        print("check: fix the draft before it is rendered")
        return 1
    try:
        fitted = render.fit(folder, b, spec, m, sel, w, selection.load_edits(folder))
    except (RuntimeError, selection.SelectionError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if fitted.measured_lines_per_page:
        render.save_calibration(p.data / render.CALIBRATION_FILE, sel.paper, fitted.measured_lines_per_page, spec.key)
    if fitted.dropped:  # the fitted page is now the plan and the draft
        sel, w = fitted.selection, fitted.writing
        sel_path.write_text(sel.model_dump_json(indent=2) + "\n", encoding="utf-8")
        draft_path.write_text(w.model_dump_json(indent=2) + "\n", encoding="utf-8")
        (folder / selection.PLAN_FILE).write_text(selection.render_plan(b, spec, m, sel), encoding="utf-8")
        (folder / write.DRAFT_FILE).write_text(write.render_draft(b, spec, sel, w), encoding="utf-8")
        print(f"fit: took {', '.join(fitted.dropped)} off the page to fit; plan and draft updated")
    final = folder / fitted.pdf.name
    final.write_bytes(fitted.pdf.read_bytes())
    final.with_suffix(".txt").write_text(fitted.layout.plain(), encoding="utf-8")
    prov = assemble.provenance(b, spec, sel, w)
    (folder / assemble.PROVENANCE_FILE).write_text(prov.model_dump_json(indent=2) + "\n", encoding="utf-8")
    checks += assemble.check(prov, sel, p.career_record)
    expected = render.expected_lines(b, sel, w)
    checks += render.extraction_findings(fitted.layout, expected, render.contact_details(b), sel.pages)
    docx = render.write_docx(final.with_suffix(".docx"), b, sel, w, fitted.design)
    checks += render.text_findings(render.docx_text(docx), expected, render.contact_details(b), "DOCX")
    ats_lines = []
    if ats.available():
        for path in (final, docx):
            try:
                result, found = ats.parse_check(path)
            except ats.RMError as err:
                checks.append(Finding(check="ats", severity="warning", message=f"RM parse check skipped: {err}"))
                continue
            checks += found
            ats_lines.append(f"Resume-Matcher parse check, {path.name}: {result.get('overall_score')}/100")
        try:
            scored, found = ats.score(spec, b, sel, w)
            (folder / ats.SCORE_FILE).write_text(json.dumps(scored, indent=2) + "\n", encoding="utf-8")
            checks += found
            ats_lines += ats.score_lines(scored)
        except ats.RMError as err:
            checks.append(Finding(check="ats", severity="warning", message=f"RM score skipped: {err}"))
    else:
        checks.append(
            Finding(check="ats", severity="warning", message="Resume-Matcher isn't available; no parse check")
        )
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
    traced = sum(len(x.claims) for x in prov.lines)
    fit_lines = [*fitted.steps, f"PDF: {final.name}; DOCX: {docx.name}; text: {final.with_suffix('.txt').name}",
                 f"Provenance: {len(prov.lines)} page lines, {traced} claim links -> {assemble.PROVENANCE_FILE}",
                 *ats_lines]  # fmt: skip
    report = folder / verify.REPORT_FILE
    report.write_text(verify.render_report(spec, w, checks, review, review_findings, fit_lines), encoding="utf-8")
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


def _folder_for_url(p: paths.Paths, url: str) -> Path | None:
    """The application folder already holding this posting, so a rerun resumes rather than refetches."""
    for path in sorted(p.applications.glob("*/posting.json")):
        posting = Posting.model_validate_json(path.read_text(encoding="utf-8"))
        if url in (posting.url, posting.api_url):
            return path.parent
    return None


def _plan_stale(folder: Path) -> bool:
    """True if there is no plan or its inputs changed after it. A current plan is shown at the gate as it is:
    re-planning on a later calibration would reshuffle a page the draft was written for."""
    plan = folder / selection.SELECTION_FILE
    inputs = [folder / n for n in assemble.GATES["plan"] if (folder / n).is_file()]
    return not plan.is_file() or any(x.stat().st_mtime > plan.stat().st_mtime for x in inputs)


def _gate(folder: Path, gate: str, what: str) -> None:
    stale = gate in assemble.approvals(folder)
    print(f"gate {'1 (plan)' if gate == 'plan' else '2 (final)'}: review {what} in {folder}"
          + (" (changed since it was approved)" if stale else ""))  # fmt: skip
    print(f"approve with `tailor run {folder.name} --approve`")


def cmd_status(args: argparse.Namespace) -> int:
    p = paths.load()
    folders = sorted(f.parent for f in p.applications.glob("*/posting.json"))
    if not folders:
        print(f"no applications in {p.applications} yet; start one with `tailor run <posting url>`")
        return 0
    names = assemble.short_names(folders)
    width = max(len(n) for n in names.values())
    for f in folders:
        r = assemble.status(f, names[f])
        extra = " ".join(x for x in (f"grade {r.grade}" if r.grade else "",
                                     f"ATS {r.ats_score:g}" if r.ats_score is not None else "") if x)  # fmt: skip
        print(f"{names[f]:<{width}}  {r.stage:<8}  {extra:<16}  {r.next}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    p = paths.load()
    if not Path(args.source).expanduser().exists() and (p.jobs / args.source).is_file():
        args.source = str(p.jobs / args.source)  # a job description dropped in HAHA_DATA_DIR/jobs/
    source = Path(args.source).expanduser()
    folder = resolve_application(p, args.source) if not source.is_file() else None
    if folder is None and "://" in args.source:
        folder = _folder_for_url(p, args.source)
    if folder is None:
        if not source.is_file() and "://" not in args.source:
            print(f"error: {args.source!r} is neither a posting URL, a file, nor one application folder",
                  file=sys.stderr)  # fmt: skip
            return 1
        folder = ingest_posting(p, args.source, args.company)
        if folder is None:
            return 1
    name = argparse.Namespace(folder=str(folder))
    if not (folder / jobspec.SPEC_FILE).is_file():
        jobspec.write_pack(folder)
        print(f"next: fill {folder / jobspec.SPEC_FILE} from {jobspec.PACK_FILE}, then `tailor run {folder.name}`")
        return 0
    if rc := cmd_job(argparse.Namespace(source=None, check=str(folder), company=None)):
        return rc
    approve = args.approve
    if not assemble.approved(folder, "plan"):
        if _plan_stale(folder):
            if rc := cmd_plan(name):
                return rc
            if not (folder / match.MATCH_FILE).is_file():
                return 0  # cmd_plan said which pack to fill
        if not approve:
            _gate(folder, "plan", f"{selection.PLAN_FILE} and {match.GAP_FILE}")
            print(f"to change the plan, edit {selection.EDITS_FILE} (pin, ban, pages) and rerun")
            return 0
        assemble.approve(folder, "plan")
        approve = False  # one gate per approval
        print("plan approved")
    if rc := cmd_write(name):
        return rc
    if not (folder / write.WRITING_FILE).is_file():
        return 0
    if (rc := cmd_check(name)) or not (folder / verify.REVIEW_FILE).is_file():
        return rc
    if not assemble.approved(folder, "final"):
        if not approve:
            _gate(folder, "final", f"{verify.REPORT_FILE} and the PDF")
            return 0
        assemble.approve(folder, "final")
        print("final check approved")
    when = assemble.approvals(folder)
    pdf = next(folder.glob("*.pdf"), None)
    print(f"ready: {pdf or folder} (plan approved {when['plan']['at']}, final approved {when['final']['at']})")
    return 0


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(main_run(argv))


def main_run(argv: list[str] | None = None) -> int:
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
        if name == "run":
            cmd.add_argument("source", help="posting URL, a file (or a file name in jobs/), or an application folder")
            cmd.add_argument("--approve", action="store_true", help="approve the gate the run stops at")
            cmd.add_argument("--company", help="company name to use instead of the board's")
    args = parser.parse_args(argv)
    handlers = {"bank": cmd_bank, "job": cmd_job, "plan": cmd_plan, "write": cmd_write, "check": cmd_check,
                "run": cmd_run, "status": cmd_status}  # fmt: skip
    return handlers[args.command](args)


if __name__ == "__main__":
    main()

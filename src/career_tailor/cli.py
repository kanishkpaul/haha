"""Command-line entry point: `uv run tailor <command>`."""

import argparse
import sys

from . import bank, paths

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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tailor", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        cmd = sub.add_parser(name, help=help_text)
        if name == "bank":
            cmd.add_argument("--report", action="store_true", help="rewrite REPORT.md from the saved bank only")
    args = parser.parse_args(argv)
    if args.command == "bank":
        raise SystemExit(cmd_bank(args))
    raise SystemExit(f"tailor {args.command}: not implemented yet")


if __name__ == "__main__":
    main()

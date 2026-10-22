"""Command-line entry point: `uv run tailor <command>`."""

import argparse

COMMANDS = {
    "bank": "Compile the career record into the evidence bank",
    "masters": "Build and load the five role-track masters",
    "job": "Analyse a job description into a job spec",
    "plan": "Gap report and selection plan for a job",
    "write": "Write the tailored resume for an approved plan",
    "check": "Verify, render, parse-check and score an application",
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="tailor", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    args = parser.parse_args()
    raise SystemExit(f"tailor {args.command}: not implemented yet")


if __name__ == "__main__":
    main()

"""Stage 7: wrap the Resume-Matcher CLI (bin/rm) for scoring and parse checks.

`call` runs one tool and returns its JSON; `parse_check` asks RM to parse-check
a PDF or DOCX the way an ATS would read it, and turns its failed checks into
findings. Nothing here writes to RM's database.
"""

import json
import subprocess
from pathlib import Path

from .models import Finding
from .paths import RM_CLI


class RMError(RuntimeError):
    pass


def call(tool: str, args: dict, timeout: float = 120) -> dict:
    done = subprocess.run([str(RM_CLI), tool, json.dumps(args)], capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise RMError(f"bin/rm {tool} failed: {(done.stderr or done.stdout).strip()[-800:]}")
    return json.loads(done.stdout)


def available() -> bool:
    return RM_CLI.exists()


def parse_check(path: Path) -> tuple[dict, list[Finding]]:
    """RM's parse check of a rendered file, with failed or warned checks as findings."""
    result = call("parse_check_file", {"file_path": str(path.resolve())})
    findings = []
    for check in result.get("checks", []):
        if check.get("status") in ("pass", "skip", "not_applicable"):
            continue
        severity = "error" if check.get("severity") == "high" and check.get("status") == "fail" else "warning"
        params = ", ".join(f"{k}={v}" for k, v in (check.get("params") or {}).items())
        findings.append(Finding(check="ats", severity=severity,
                                message=f"{path.suffix[1:].upper()} {check.get('id')}: {check.get('status')}"
                                        + (f" ({params})" if params else "")))  # fmt: skip
    return result, findings

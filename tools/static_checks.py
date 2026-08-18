#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import re
import sys
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
errors: list[str] = []

# YAML syntax. Jinja-bearing Ansible YAML is intentionally written so that it
# remains valid YAML before template evaluation.
for path in sorted((ROOT / "ansible").rglob("*.yml")):
    try:
        yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 - aggregate all static failures
        errors.append(f"YAML parse failed: {path.relative_to(ROOT)}: {exc}")

for path in sorted((ROOT / ".github").rglob("*.yml")):
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Workflow YAML parse failed: {path.relative_to(ROOT)}: {exc}")

# Production reference identifiers may appear in ROADMAP history only, never in
# executable deployment content.
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or ".git" in path.parts or ".venv" in path.parts:
        continue
    if path.name == "ROADMAP.md":
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for forbidden in ("redacted" + ".ru", "203.0.113" + ".119"):
        if forbidden in text:
            errors.append(f"Hardcoded production identifier {forbidden!r}: {path.relative_to(ROOT)}")

patterns = {
    r"\bpull:\s*true\b": "Routine convergence must not force image pulls",
    r"LIVEKIT_JWT_PORT": "Deprecated JWT port variable reintroduced",
    r"\bauto_create:\s*true\b": "Insecure LiveKit room auto-create reintroduced",
    r"ghcr\.io/etkecc/synapse-admin": "Old Synapse Admin image reintroduced",
    r"\blivekit_ws_port\b": "Removed LiveKit variable reintroduced",
    r"\belement_jwt_port\b": "Removed JWT variable reintroduced",
}

for path in sorted((ROOT / "ansible").rglob("*")):
    if not path.is_file():
        continue
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        continue
    for pattern, message in patterns.items():
        if re.search(pattern, text):
            errors.append(f"{message}: {path.relative_to(ROOT)}")

latest_refs: list[tuple[pathlib.Path, str]] = []
for path in sorted((ROOT / "ansible").rglob("*")):
    if not path.is_file():
        continue
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        continue
    for line in text.splitlines():
        if ":latest" in line:
            latest_refs.append((path, line.strip()))

if len(latest_refs) != 1 or "ghcr.io/etkecc/ketesa:latest" not in latest_refs[0][1]:
    rendered = ", ".join(f"{p.relative_to(ROOT)}: {line}" for p, line in latest_refs) or "none"
    errors.append(f"Unexpected :latest image references; Ketesa must be the only explicit exception: {rendered}")

if errors:
    print("STATIC CHECKS FAILED", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)

print("Static checks: OK")

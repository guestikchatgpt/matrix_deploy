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
    r":latest(?:[\"']|\s|$)": "Moving :latest image reference reintroduced",
    r"ansible\.builtin\.apt_repository\b": "Deprecated apt_repository module reintroduced",
    r"\bansible_(?:distribution|distribution_version|distribution_release|effective_user_id|memtotal_mb|processor_vcpus|processor_count|mounts|architecture|date_time)\b": (
        "Deprecated top-level injected Ansible fact reintroduced; use ansible_facts[...]"
    ),
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

# Avoid the SIGPIPE false-negative class already observed with pipefail. Shell
# code should capture producer output first, then grep a here-string/file.
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or path.suffix not in {".sh", ".j2"}:
        continue
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        continue
    if re.search(r"\|\s*grep\b", text):
        errors.append(f"Unsafe producer | grep pipeline: {path.relative_to(ROOT)}")

if errors:
    print("STATIC CHECKS FAILED", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)

print("Static checks: OK")

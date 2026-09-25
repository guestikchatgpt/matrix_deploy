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
    r"delayed_leave_event_(?:delay|restart)": "Element Call <0.26 matrix_rtc_session key reintroduced (use delayed_leave.*)",
    r"feature_use_device_session_member_events": "Removed Element Call feature flag reintroduced",
    r"\buse_presence:": "Deprecated Synapse use_presence reintroduced (use presence.enabled)",
    r"\"homeserverUrl\"": "Old synapse-admin config key reintroduced (Ketesa uses restrictBaseUrl)",
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

# Version policy must describe upstream sources only. Exact application tags are
# runtime state resolved by preflight into /etc/matrix-deploy/versions.yml; they
# must not creep back into the repository as top-level *_image pins.
versions_path = ROOT / "ansible/inventory/group_vars/all/versions.yml"
try:
    version_policy = yaml.safe_load(versions_path.read_text(encoding="utf-8")) or {}
except Exception as exc:  # noqa: BLE001
    errors.append(f"Cannot parse version policy: {exc}")
    version_policy = {}

if isinstance(version_policy, dict):
    hardcoded_image_keys = sorted(
        key for key in version_policy
        if isinstance(key, str) and key.endswith("_image")
    )
    if hardcoded_image_keys:
        errors.append(
            "Hardcoded top-level application image pins reintroduced in versions.yml: "
            + ", ".join(hardcoded_image_keys)
        )

    if version_policy.get("matrix_version_lock_file") != "/etc/matrix-deploy/versions.yml":
        errors.append("matrix_version_lock_file must remain /etc/matrix-deploy/versions.yml")

    postgresql_major = str(version_policy.get("postgresql_major", ""))
    if not re.fullmatch(r"\d+", postgresql_major):
        errors.append(f"Invalid postgresql_major in version policy: {postgresql_major!r}")

    sources = version_policy.get("matrix_version_sources")
    required_sources = {
        "postgresql",
        "synapse",
        "element_web",
        "element_call",
        "livekit",
        "element_jwt",
        "synad",
    }
    if not isinstance(sources, dict):
        errors.append("matrix_version_sources must be a mapping")
    else:
        actual_sources = set(sources)
        if actual_sources != required_sources:
            errors.append(
                "matrix_version_sources mismatch: "
                f"expected={sorted(required_sources)} actual={sorted(actual_sources)}"
            )

        # These are upstream prerelease shapes observed in the Matrix stack,
        # plus generic alpha/beta/dev forms. Every github_release policy must
        # reject all of them. This protects the stable-only contract even if an
        # upstream release is accidentally not marked as `prerelease` by GitHub.
        prerelease_examples = {
            "synapse": (
                "v1.158.0rc1",
                "v1.153.0rc3",
                "v1.159.0-beta.1",
            ),
            "element_web": (
                "v1.12.19-rc.0",
                "v1.12.16-rc.1",
                "v1.12.26-beta.1",
            ),
            "element_call": (
                "v0.19.2-rc.1",
                "v0.19.1-rc2",
                "v0.24.0-beta.1",
            ),
            "livekit": (
                "v1.13.5-rc.1",
                "v1.13.5-beta.1",
                "v1.13.5-alpha.1",
            ),
            "element_jwt": (
                "v0.5.0-rc1",
                "v0.5.0-beta.1",
                "v0.5.0-dev.1",
            ),
            "synad": (
                "v1.4.0-rc1",
                "v1.4.0-beta.1",
                "v1.4.0-dev.1",
            ),
        }
        stable_examples = {
            "synapse": "v1.159.0",
            "element_web": "v1.12.26",
            "element_call": "v0.24.0",
            "livekit": "v1.13.5",
            "element_jwt": "v0.5.0",
            "synad": "v1.4.0",
        }

        output_vars: set[str] = set()
        for name, raw_source in sources.items():
            if not isinstance(raw_source, dict):
                errors.append(f"Version source {name!r} must be a mapping")
                continue

            image = str(raw_source.get("image", "")).strip()
            if not image:
                errors.append(f"Version source {name!r} is missing image repository")
            else:
                last_component = image.rsplit("/", 1)[-1]
                if ":" in last_component or "@" in image:
                    errors.append(
                        f"Version source {name!r} must contain an untagged image repository: {image!r}"
                    )

            output_var = str(raw_source.get("output_image_var", "")).strip()
            if not output_var.endswith("_image"):
                errors.append(
                    f"Version source {name!r} has invalid output_image_var: {output_var!r}"
                )
            elif output_var in output_vars:
                errors.append(f"Duplicate output_image_var in version policy: {output_var}")
            else:
                output_vars.add(output_var)

            resolver = str(raw_source.get("resolver", ""))
            min_version = str(raw_source.get("min_version", "")).strip()
            if not min_version:
                errors.append(f"Version source {name!r} is missing min_version")
            elif resolver == "postgresql_dockerhub":
                if not re.fullmatch(rf"{re.escape(postgresql_major)}\.\d+", min_version):
                    errors.append(
                        f"PostgreSQL min_version {min_version!r} must be a stable "
                        f"{postgresql_major}.N release inside postgresql_major"
                    )
            elif not re.fullmatch(str(raw_source.get("release_regex", "")), min_version):
                errors.append(
                    f"Version source {name!r} min_version {min_version!r} is not a "
                    "stable tag accepted by its release_regex"
                )

            if resolver not in {"github_release", "postgresql_dockerhub"}:
                errors.append(f"Unsupported resolver in version policy for {name!r}: {resolver!r}")

            if resolver == "github_release":
                if not raw_source.get("github_repo"):
                    errors.append(f"Version source {name!r} is missing github_repo")
                if raw_source.get("tag_transform") not in {"identity", "strip_v"}:
                    errors.append(f"Version source {name!r} has invalid tag_transform")

                release_regex = str(raw_source.get("release_regex", "")).strip()
                if not release_regex:
                    errors.append(f"Version source {name!r} is missing stable release_regex")
                    continue

                try:
                    stable_pattern = re.compile(release_regex)
                except re.error as exc:
                    errors.append(
                        f"Version source {name!r} has invalid release_regex {release_regex!r}: {exc}"
                    )
                    continue

                stable_example = stable_examples.get(name)
                if stable_example and not stable_pattern.fullmatch(stable_example):
                    errors.append(
                        f"Stable release regex for {name!r} rejects expected stable tag "
                        f"{stable_example!r}: {release_regex!r}"
                    )

                for prerelease_tag in prerelease_examples.get(name, ()):
                    if stable_pattern.fullmatch(prerelease_tag):
                        errors.append(
                            f"Stable release regex for {name!r} accepts prerelease tag "
                            f"{prerelease_tag!r}: {release_regex!r}"
                        )

        # PostgreSQL is resolved from Docker Official Images rather than GitHub
        # Releases. Its resolver accepts only numeric <major>.<patch>[.<patch>]
        # tags, therefore official-image tags such as 19beta2 are excluded by
        # construction. Keep the configured major numeric so that remains true.
        postgres_source = sources.get("postgresql")
        if isinstance(postgres_source, dict):
            if postgres_source.get("resolver") != "postgresql_dockerhub":
                errors.append("PostgreSQL must keep the postgresql_dockerhub resolver")
else:
    errors.append("Version policy YAML root must be a mapping")

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

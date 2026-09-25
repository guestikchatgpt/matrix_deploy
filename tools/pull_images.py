#!/usr/bin/env python3
"""Pull the exact images of the version lock, falling back to other sources.

For every locked component the image is pulled by manifest digest from, in
order: the upstream registry, official alternate registries (same image
published elsewhere by its maintainers) and configured public mirrors. The
pulled image is then tagged with the canonical reference the containers use,
so docker_container never needs network access for it.

Pulling by digest makes the source irrelevant for integrity: Docker verifies
the content against the digest recorded by preflight. Mirrors are therefore
only used when the lock carries a digest.

Output: one line per component; lines starting with "PULLED " mean a change.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from typing import Any

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import registry  # noqa: E402


class PullError(RuntimeError):
    pass


def docker(*args: str, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def local_repo_digests(reference: str) -> list[str] | None:
    """RepoDigests of a local image, or None when it is not present."""
    result = docker("image", "inspect", reference, "--format", "{{json .RepoDigests}}")
    if result.returncode != 0:
        return None
    try:
        return list(json.loads(result.stdout.strip() or "[]") or [])
    except json.JSONDecodeError:
        return []


def has_digest(repo_digests: list[str] | None, digest: str) -> bool:
    return bool(repo_digests) and any(entry.endswith(f"@{digest}") for entry in repo_digests or [])


def try_pull(reference: str, platform: str, timeout: int, attempts: int) -> str:
    """Return "" on success, otherwise the last error."""
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            result = docker("pull", "--platform", platform, reference, timeout=timeout)
        except subprocess.TimeoutExpired:
            last = f"timed out after {timeout}s"
        else:
            if result.returncode == 0:
                return ""
            last = (result.stderr or result.stdout).strip().splitlines()[-1:] or [f"exit {result.returncode}"]
            last = last[0][-300:]
        if attempt < attempts:
            time.sleep(5 * attempt)
    return last or "unknown error"


def pull_component(
    name: str,
    item: dict[str, Any],
    policy_source: dict[str, Any],
    mirrors: dict[str, list[str]],
    arch: str,
    timeout: int,
) -> str:
    image = str(item["image"])
    digest = str(item.get("digest") or "")
    ref = registry.parse_image(image)
    platform = f"linux/{arch}"

    present = local_repo_digests(image)
    if present is not None and (not digest or has_digest(present, digest)):
        return f"PRESENT {name} {image}"

    sources = registry.candidate_sources(
        image, policy_source.get("alternate_images") or [], mirrors
    )
    if not digest:
        # Without a recorded digest a mirror's content cannot be verified.
        sources = [source for source in sources if source.kind != "mirror"]

    errors = []
    for source in sources:
        reference = source.pinned(digest) if digest else source.tagged(ref.tag)
        attempts = 3 if source.kind == "upstream" else 2
        error = try_pull(reference, platform, timeout, attempts)
        if error:
            errors.append(f"{source.kind} {reference}: {error}")
            print(f"  {name}: {source.kind} {source.repository} failed: {error}", flush=True)
            continue
        tagged = docker("tag", reference, image)
        if tagged.returncode != 0:
            raise PullError(f"{name}: docker tag {reference} {image} failed: {tagged.stderr.strip()}")
        if digest and not has_digest(local_repo_digests(image), digest):
            raise PullError(f"{name}: {image} does not carry the locked digest {digest} after pull")
        return f"PULLED {name} {image} from {source.kind} {source.repository}"

    hint = "" if digest else " (lock has no digest, so mirrors were not used; rerun converge to pin digests)"
    raise PullError(f"{name}: {image} could not be pulled from any source{hint}: " + "; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", required=True, type=pathlib.Path)
    parser.add_argument("--policy", required=True, type=pathlib.Path)
    parser.add_argument("--mirrors-json", default="{}")
    parser.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    parser.add_argument("--timeout", type=int, default=600, help="seconds per docker pull attempt")
    args = parser.parse_args()

    lock = yaml.safe_load(args.lock.read_text(encoding="utf-8")) or {}
    policy = yaml.safe_load(args.policy.read_text(encoding="utf-8")) or {}
    mirrors = json.loads(args.mirrors_json or "{}") or {}
    components = (lock.get("matrix_version_lock") or {}).get("components") or {}
    sources = policy.get("matrix_version_sources") or {}
    if not components:
        raise PullError(f"no components in version lock {args.lock}")

    failures = []
    for name, item in components.items():
        try:
            print(pull_component(str(name), item, dict(sources.get(name) or {}), mirrors, args.arch, args.timeout), flush=True)
        except (PullError, registry.RegistryError) as exc:
            failures.append(str(exc))
            print(f"FAILED {exc}", flush=True)
    if failures:
        print(
            "Some images could not be pulled. Check connectivity to the registries, "
            "add mirrors in matrix_registry_mirrors or set matrix_registry_proxy.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PullError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

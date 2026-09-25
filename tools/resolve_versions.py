#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import platform
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import registry  # noqa: E402

USER_AGENT = "matrix-deploy-version-resolver/1"
GITHUB_API_VERSION = "2022-11-28"


class ResolveError(RuntimeError):
    pass


def http_request(url: str, *, github: bool = False) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json" if github else "application/json",
        "User-Agent": USER_AGENT,
    }
    if github:
        headers["X-GitHub-Api-Version"] = GITHUB_API_VERSION
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            # Client errors are definitive answers; retry only throttling and
            # server-side failures.
            if exc.code < 500 and exc.code != 429 or attempt == attempts:
                raise ResolveError(f"HTTP {exc.code} for {url}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == attempts:
                reason = getattr(exc, "reason", exc)
                raise ResolveError(f"request failed for {url}: {reason}") from exc
        time.sleep(3 * attempt)
    raise ResolveError(f"request failed for {url}")


def is_not_found(exc: ResolveError) -> bool:
    return str(exc).startswith("HTTP 404 ")


def http_json(url: str, *, github: bool = False) -> dict[str, Any]:
    try:
        data = json.loads(http_request(url, github=github).decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ResolveError(f"invalid JSON from {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise ResolveError(f"unexpected JSON object from {url}")
    return data


def github_latest_release(repo: str, release_regex: str | None) -> tuple[str, str]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    data = http_json(url, github=True)
    tag = str(data.get("tag_name", "")).strip()
    if not tag:
        raise ResolveError(f"GitHub latest release for {repo} has no tag_name")
    if data.get("draft") or data.get("prerelease"):
        raise ResolveError(f"GitHub latest release for {repo} is draft/prerelease: {tag}")
    if release_regex and not re.fullmatch(release_regex, tag):
        raise ResolveError(
            f"GitHub latest release tag {tag!r} for {repo} does not match {release_regex!r}"
        )
    return tag, str(data.get("html_url") or f"https://github.com/{repo}/releases/tag/{tag}")


def version_tuple(version: str) -> tuple[int, ...]:
    """Numeric key of a stable version such as v1.161.0, 0.7.0 or 18.6."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(version).strip())
    if not match:
        raise ResolveError(f"not a stable numeric version: {version!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def below_minimum(version: str, minimum: str) -> bool:
    return version_tuple(version) < version_tuple(minimum)


def minimum_violations(
    policy: dict[str, Any], components: dict[str, Any]
) -> list[str]:
    """Components whose version is missing or older than the policy floor."""
    violations: list[str] = []
    for name, source in policy.get("matrix_version_sources", {}).items():
        minimum = source.get("min_version") if isinstance(source, dict) else None
        if not minimum:
            continue
        item = components.get(name) if isinstance(components, dict) else None
        version = str(item.get("version", "")) if isinstance(item, dict) else ""
        if not version:
            violations.append(f"{name}: missing (requires >= {minimum})")
            continue
        try:
            if below_minimum(version, str(minimum)):
                violations.append(f"{name}: {version} < required {minimum}")
        except ResolveError as exc:
            violations.append(f"{name}: {exc}")
    return violations


def transform_release_tag(tag: str, transform: str) -> str:
    if transform == "identity":
        return tag
    if transform == "strip_v":
        return tag[1:] if tag.startswith("v") else tag
    raise ResolveError(f"unsupported tag transform: {transform}")


def dockerhub_tag(namespace: str, repository: str, tag: str, arch: str) -> dict[str, Any]:
    url = (
        "https://hub.docker.com/v2/namespaces/"
        f"{namespace}/repositories/{repository}/tags/{tag}"
    )
    data = http_json(url)
    if str(data.get("name", "")) != tag:
        raise ResolveError(
            f"Docker Hub returned unexpected tag for {namespace}/{repository}:{tag}"
        )

    images = data.get("images", [])
    if isinstance(images, dict):
        images = [images]
    if isinstance(images, list) and images:
        platforms = {
            (str(item.get("os", "")), str(item.get("architecture", "")))
            for item in images
            if isinstance(item, dict)
        }
        if ("linux", arch) not in platforms:
            raise ResolveError(
                f"Docker Hub tag {namespace}/{repository}:{tag} lacks linux/{arch}; "
                f"available={sorted(platforms)}"
            )
    return data


def dockerhub_check(namespace: str, repository: str, tag: str, arch: str) -> None:
    """Docker Hub API cross-check; only a definitive answer is fatal.

    The registry digest lookup is the authoritative existence check, so an
    unreachable Docker Hub API (network filtering, throttling) only warns.
    """
    try:
        dockerhub_tag(namespace, repository, tag, arch)
    except ResolveError as exc:
        if is_not_found(exc) or "lacks linux/" in str(exc) or "unexpected tag" in str(exc):
            raise
        print(f"WARNING: Docker Hub API cross-check skipped for {namespace}/{repository}:{tag}: {exc}")


def registry_list_tags(repository: str, *, timeout: int = 60, attempts: int = 2) -> list[str]:
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                ["skopeo", "list-tags", f"docker://{repository}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ResolveError("skopeo is required; run bootstrap.sh first") from exc
        except subprocess.TimeoutExpired:
            last = f"timed out after {timeout}s"
        else:
            if result.returncode == 0:
                try:
                    return [str(tag) for tag in json.loads(result.stdout).get("Tags", [])]
                except json.JSONDecodeError as exc:
                    raise ResolveError(f"invalid tag list from {repository}") from exc
            last = (result.stderr or "").strip()[-300:]
        if attempt < attempts:
            time.sleep(3 * attempt)
    raise ResolveError(f"cannot list tags of {repository}: {last}")


def latest_release_from_registries(
    sources: list[registry.Source], release_regex: str, transform: str
) -> tuple[str, str]:
    """Fallback when the GitHub API is unreachable: newest stable tag in a registry.

    The chosen tag is still subject to min_version and to trusted digest
    resolution, so a mirror advertising a bogus tag cannot get it deployed.
    """
    errors = []
    for source in sources:
        try:
            tags = registry_list_tags(source.repository)
        except ResolveError as exc:
            errors.append(str(exc))
            continue
        releases = []
        for tag in tags:
            release = f"v{tag}" if transform == "strip_v" and not tag.startswith("v") else tag
            if re.fullmatch(release_regex, release):
                releases.append(release)
        if releases:
            best = max(releases, key=version_tuple)
            return best, f"registry tags of {source.repository}"
        errors.append(f"{source.repository}: no stable tags")
    raise ResolveError("no registry reachable for tag listing: " + "; ".join(errors))


def postgresql_tags_from_dockerhub(namespace: str, repository: str, major: str) -> list[str]:
    url = (
        "https://hub.docker.com/v2/namespaces/"
        f"{namespace}/repositories/{repository}/tags?name={major}.&page_size=100"
    )
    data = http_json(url)
    return [str(item.get("name", "")) for item in data.get("results", []) if isinstance(item, dict)]


def resolve_postgresql(
    source: dict[str, Any],
    major: str,
    arch: str,
    sources: list[registry.Source] | None = None,
) -> dict[str, Any]:
    metadata_url = str(source["github_metadata_url"])
    namespace = str(source["dockerhub_namespace"])
    repository = str(source["dockerhub_repository"])
    pattern = re.compile(rf"^{re.escape(major)}\.(\d+(?:\.\d+)*)$")
    tags: list[str] = []
    origin = metadata_url

    try:
        raw = http_request(metadata_url).decode("utf-8", errors="strict")
        for line in raw.splitlines():
            if line.startswith("Tags:"):
                tags.extend(token.strip() for token in line.removeprefix("Tags:").split(","))
    except ResolveError as exc:
        print(f"WARNING: Docker Official Images metadata unreachable ({exc}); trying Docker Hub API")
        try:
            tags = postgresql_tags_from_dockerhub(namespace, repository, major)
            origin = f"hub.docker.com {namespace}/{repository}"
        except ResolveError as hub_exc:
            print(f"WARNING: Docker Hub API unreachable ({hub_exc}); trying registry tag lists")
            for candidate in sources or []:
                try:
                    tags = registry_list_tags(candidate.repository)
                    origin = f"registry tags of {candidate.repository}"
                    break
                except ResolveError:
                    continue

    candidates = {tuple(int(part) for part in tag.split(".")): tag for tag in tags if pattern.fullmatch(tag)}
    if not candidates:
        raise ResolveError(f"no stable PostgreSQL {major}.x tags found (last source: {origin})")

    selected_tag = candidates[max(candidates)]
    dockerhub_check(namespace, repository, selected_tag, arch)

    return {
        "version": selected_tag,
        "image": f"{source['image']}:{selected_tag}",
        "source": origin,
        "release_tag": selected_tag,
    }


def skopeo_verify(image: str, arch: str) -> None:
    command = [
        "skopeo",
        "inspect",
        "--override-os",
        "linux",
        "--override-arch",
        arch,
        f"docker://{image}",
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ResolveError(
            "skopeo is required for registry validation; run bootstrap.sh first"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ResolveError(f"registry validation timed out for {image}") from exc

    if result.returncode != 0:
        detail = (result.stderr or "").strip()[-800:]
        raise ResolveError(f"registry validation failed for {image}: {detail}")


def host_architecture() -> str:
    machine = platform.machine().lower()
    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    try:
        return mapping[machine]
    except KeyError as exc:
        raise ResolveError(f"unsupported host architecture: {machine}") from exc


def load_yaml(path: pathlib.Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ResolveError(f"cannot read YAML {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ResolveError(f"YAML root must be a mapping: {path}")
    return data


def fetch_manifest(reference: str) -> bytes:
    """Raw manifest fetch; module-level so tests can replace it."""
    return registry.skopeo_raw_manifest(reference)


def image_sources(
    source: dict[str, Any], image: str, mirrors: dict[str, list[str]] | None
) -> list[registry.Source]:
    return registry.candidate_sources(image, source.get("alternate_images") or [], mirrors)


def locate_image(
    name: str,
    source: dict[str, Any],
    image: str,
    arch: str,
    mirrors: dict[str, list[str]] | None,
) -> dict[str, str]:
    """Pin `image` to a manifest digest obtained from a trusted source."""
    sources = image_sources(source, image, mirrors)
    try:
        located = registry.locate_digest(image, sources, fetch=fetch_manifest, log=print)
    except registry.RegistryError as exc:
        raise ResolveError(f"{name}: {exc}") from exc
    if located.platforms is None:
        skopeo_verify(located.source.pinned(located.digest), arch)
    elif ("linux", arch) not in located.platforms:
        raise ResolveError(
            f"{name}: {image} has no linux/{arch} image; available={sorted(located.platforms)}"
        )
    if located.source.kind != "upstream":
        print(f"  {name}: upstream registry unreachable, digest from {located.source.repository}")
    return {"digest": located.digest, "digest_source": located.source.repository}


def resolve(
    policy: dict[str, Any],
    arch: str,
    mirrors: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    sources = policy.get("matrix_version_sources")
    if not isinstance(sources, dict) or not sources:
        raise ResolveError("matrix_version_sources is missing from version policy")

    postgresql_major = str(policy.get("postgresql_major", "")).strip()
    if not re.fullmatch(r"\d+", postgresql_major):
        raise ResolveError(f"invalid postgresql_major: {postgresql_major!r}")

    components: dict[str, dict[str, Any]] = {}
    output_vars: dict[str, Any] = {
        "postgresql_version": postgresql_major,
    }
    resolved_images: list[str] = []

    for name, raw_source in sources.items():
        if not isinstance(raw_source, dict):
            raise ResolveError(f"version source {name} must be a mapping")
        source = dict(raw_source)
        resolver = str(source.get("resolver", "github_release"))
        # Sources for tag listing fallbacks (the tag itself is irrelevant here).
        repo_sources = image_sources(source, f"{source['image']}:latest", mirrors)

        if resolver == "postgresql_dockerhub":
            component = resolve_postgresql(source, postgresql_major, arch, repo_sources)
        elif resolver == "github_release":
            repo = str(source["github_repo"])
            release_regex = str(source.get("release_regex", "")) or None
            transform = str(source.get("tag_transform", "identity"))
            try:
                release_tag, release_url = github_latest_release(repo, release_regex)
            except ResolveError as exc:
                if not release_regex or "does not match" in str(exc) or "draft/prerelease" in str(exc):
                    raise
                print(f"WARNING: GitHub API unavailable for {repo} ({exc}); using registry tags")
                release_tag, release_url = latest_release_from_registries(
                    repo_sources, release_regex, transform
                )
            docker_tag = transform_release_tag(release_tag, transform)
            image = f"{source['image']}:{docker_tag}"

            dockerhub_namespace = source.get("dockerhub_namespace")
            dockerhub_repository = source.get("dockerhub_repository")
            if dockerhub_namespace and dockerhub_repository:
                dockerhub_check(
                    str(dockerhub_namespace),
                    str(dockerhub_repository),
                    docker_tag,
                    arch,
                )

            component = {
                "version": release_tag,
                "image": image,
                "source": release_url,
                "release_tag": release_tag,
            }
        else:
            raise ResolveError(f"unsupported resolver {resolver!r} for {name}")

        minimum = source.get("min_version")
        if minimum and below_minimum(str(component["version"]), str(minimum)):
            raise ResolveError(
                f"{name}: latest stable upstream release {component['version']} is older "
                f"than the verified minimum {minimum}"
            )

        component.update(locate_image(str(name), source, str(component["image"]), arch, mirrors))
        components[str(name)] = component
        resolved_images.append(str(component["image"]))

        output_var = str(source.get("output_image_var", f"{name}_image"))
        output_vars[output_var] = component["image"]

        version_var = source.get("output_version_var")
        if version_var:
            output_vars[str(version_var)] = component["version"]

    lock = {
        "matrix_version_lock": {
            "schema": 1,
            "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "architecture": arch,
            "postgresql_major": postgresql_major,
            "components": components,
        },
        **output_vars,
        "matrix_resolved_images": resolved_images,
    }
    return lock


def add_missing_digests(
    policy: dict[str, Any],
    lock: dict[str, Any],
    arch: str,
    mirrors: dict[str, list[str]] | None,
) -> list[str]:
    """Pin locks written before digests existed; versions stay unchanged."""
    added = []
    components = lock.get("matrix_version_lock", {}).get("components", {})
    for name, source in policy.get("matrix_version_sources", {}).items():
        item = components.get(name)
        if not isinstance(item, dict) or item.get("digest"):
            continue
        item.update(locate_image(str(name), dict(source), str(item["image"]), arch, mirrors))
        added.append(f"{name}: {item['image']}@{item['digest']}")
    return added


def write_lock(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def compare_locks(current: dict[str, Any], latest: dict[str, Any]) -> list[str]:
    current_components = (
        current.get("matrix_version_lock", {}).get("components", {})
        if isinstance(current.get("matrix_version_lock"), dict)
        else {}
    )
    latest_components = latest["matrix_version_lock"]["components"]

    changes: list[str] = []
    for name, latest_item in latest_components.items():
        current_item = current_components.get(name, {}) if isinstance(current_components, dict) else {}
        current_version = str(current_item.get("version", "missing"))
        latest_version = str(latest_item.get("version", "unknown"))
        if current_version != latest_version:
            changes.append(f"{name}: {current_version} -> {latest_version}")
    return changes


def print_selected(data: dict[str, Any], *, prefix: str) -> None:
    components = data["matrix_version_lock"]["components"]
    print(prefix)
    for name, item in components.items():
        digest = str(item.get("digest", ""))[:19]
        print(f"  {name:14s} {item['version']:16s} {item['image']}  {digest}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve stable Matrix stack versions")
    parser.add_argument("--policy", required=True, type=pathlib.Path)
    parser.add_argument("--lock", required=True, type=pathlib.Path)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    parser.add_argument(
        "--mirrors-json",
        default="{}",
        help='registry mirrors per upstream registry, e.g. {"ghcr.io": ["ghcr.nju.edu.cn"]}',
    )
    args = parser.parse_args()

    if args.refresh == args.check:
        parser.error("choose exactly one of --refresh or --check")

    policy = load_yaml(args.policy)
    arch = args.arch or host_architecture()
    try:
        mirrors = json.loads(args.mirrors_json or "{}") or {}
    except json.JSONDecodeError as exc:
        raise ResolveError(f"invalid --mirrors-json: {exc}") from exc
    if not isinstance(mirrors, dict):
        raise ResolveError("--mirrors-json must be a JSON object")

    if args.refresh:
        latest = resolve(policy, arch, mirrors)
        write_lock(args.lock, latest)
        print_selected(latest, prefix="Resolved stable versions:")
        print(f"LOCK_UPDATED {args.lock}")
        return 0

    if not args.lock.is_file():
        raise ResolveError(f"version lock is missing: {args.lock}")

    current = load_yaml(args.lock)
    lock_meta = current.get("matrix_version_lock")
    current_components = lock_meta.get("components", {}) if isinstance(lock_meta, dict) else {}
    violations = minimum_violations(policy, current_components)
    if violations:
        # Fail closed: the rendered configuration requires these versions, so a
        # routine converge must not apply it to older locked images.
        raise ResolveError(
            "locked versions are older than this playbook supports: "
            + "; ".join(violations)
            + ". Run upgrade.sh (backup + lock refresh) before converging."
        )

    # Locks created before digest pinning: add digests for the *locked* images
    # (no version change) so pulls can safely fall back to mirrors.
    try:
        added = add_missing_digests(policy, current, arch, mirrors)
    except ResolveError as exc:
        added = []
        print(f"WARNING: could not pin locked images to digests yet: {exc}")
    if added:
        write_lock(args.lock, current)
        print("Pinned locked images to manifest digests (versions unchanged):")
        for line in added:
            print(f"  {line}")
        print(f"LOCK_DIGESTS_ADDED {args.lock}")

    try:
        latest = resolve(policy, arch, mirrors)
    except ResolveError as exc:
        print(f"WARNING: upstream version check failed; keeping locked versions: {exc}")
        return 0

    changes = compare_locks(current, latest)
    if changes:
        print("Updates available (lock is unchanged):")
        for change in changes:
            print(f"  {change}")
    else:
        print("Locked versions are current according to upstream stable releases.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ResolveError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

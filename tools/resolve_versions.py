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
import urllib.error
import urllib.request
from typing import Any

import yaml

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
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise ResolveError(f"HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ResolveError(f"request failed for {url}: {exc.reason}") from exc


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


def resolve_postgresql(source: dict[str, Any], major: str, arch: str) -> dict[str, Any]:
    metadata_url = str(source["github_metadata_url"])
    raw = http_request(metadata_url).decode("utf-8", errors="strict")

    candidates: set[tuple[int, ...]] = set()
    exact_tags: dict[tuple[int, ...], str] = {}
    pattern = re.compile(rf"^{re.escape(major)}\.(\d+(?:\.\d+)*)$")

    for line in raw.splitlines():
        if not line.startswith("Tags:"):
            continue
        for token in line.removeprefix("Tags:").split(","):
            tag = token.strip()
            match = pattern.fullmatch(tag)
            if not match:
                continue
            version_tuple = tuple(int(part) for part in tag.split("."))
            candidates.add(version_tuple)
            exact_tags[version_tuple] = tag

    if not candidates:
        raise ResolveError(
            f"no stable PostgreSQL {major}.x tags found in Docker Official Images metadata"
        )

    selected_tuple = max(candidates)
    selected_tag = exact_tags[selected_tuple]
    namespace = str(source["dockerhub_namespace"])
    repository = str(source["dockerhub_repository"])
    dockerhub_tag(namespace, repository, selected_tag, arch)

    return {
        "version": selected_tag,
        "image": f"{source['image']}:{selected_tag}",
        "source": metadata_url,
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


def resolve(policy: dict[str, Any], arch: str) -> dict[str, Any]:
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

        if resolver == "postgresql_dockerhub":
            component = resolve_postgresql(source, postgresql_major, arch)
        elif resolver == "github_release":
            repo = str(source["github_repo"])
            release_tag, release_url = github_latest_release(
                repo,
                str(source.get("release_regex", "")) or None,
            )
            docker_tag = transform_release_tag(
                release_tag,
                str(source.get("tag_transform", "identity")),
            )
            image = f"{source['image']}:{docker_tag}"

            dockerhub_namespace = source.get("dockerhub_namespace")
            dockerhub_repository = source.get("dockerhub_repository")
            if dockerhub_namespace and dockerhub_repository:
                dockerhub_tag(
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

        skopeo_verify(str(component["image"]), arch)
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
        print(f"  {name:14s} {item['version']:16s} {item['image']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve stable Matrix stack versions")
    parser.add_argument("--policy", required=True, type=pathlib.Path)
    parser.add_argument("--lock", required=True, type=pathlib.Path)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    args = parser.parse_args()

    if args.refresh == args.check:
        parser.error("choose exactly one of --refresh or --check")

    policy = load_yaml(args.policy)
    arch = args.arch or host_architecture()

    if args.refresh:
        latest = resolve(policy, arch)
        write_lock(args.lock, latest)
        print_selected(latest, prefix="Resolved stable versions:")
        print(f"LOCK_UPDATED {args.lock}")
        return 0

    if not args.lock.is_file():
        raise ResolveError(f"version lock is missing: {args.lock}")

    current = load_yaml(args.lock)
    try:
        latest = resolve(policy, arch)
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

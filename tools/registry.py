"""Registry helpers shared by the version resolver and the image puller.

Images are always addressed by manifest digest once resolved, so any source
(the upstream registry, an official alternate registry or a public mirror)
can only deliver the exact bytes that were recorded in the version lock:
content addressing makes a substituted image fail verification.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

DOCKER_HUB = "docker.io"
INDEX_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageRef:
    registry: str
    path: str
    tag: str

    @property
    def repository(self) -> str:
        return f"{self.registry}/{self.path}"

    @property
    def canonical(self) -> str:
        """The reference containers are started with (tag form)."""
        return f"{self.repository}:{self.tag}"


def parse_image(reference: str) -> ImageRef:
    """Split `[registry/]path:tag` with Docker Hub normalisation."""
    name, sep, tag = reference.rpartition(":")
    if not sep or "/" in tag:
        raise RegistryError(f"image reference must carry a tag: {reference!r}")
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, path = first, rest
    else:
        registry, path = DOCKER_HUB, name
    if registry == DOCKER_HUB and "/" not in path:
        path = f"library/{path}"
    return ImageRef(registry, path, tag)


def parse_repository(repository: str) -> tuple[str, str]:
    ref = parse_image(f"{repository}:x")
    return ref.registry, ref.path


@dataclass(frozen=True)
class Source:
    kind: str  # "upstream" | "alternate" | "mirror"
    repository: str  # registry/path without tag

    def tagged(self, tag: str) -> str:
        return f"{self.repository}:{tag}"

    def pinned(self, digest: str) -> str:
        return f"{self.repository}@{digest}"


def candidate_sources(
    image: str,
    alternates: Iterable[str] = (),
    mirrors: dict[str, list[str]] | None = None,
) -> list[Source]:
    """Upstream first, then official alternate registries, then mirrors.

    Mirrors are listed per upstream registry host and rewrite only the host,
    e.g. ghcr.io/element-hq/element-call -> ghcr.nju.edu.cn/element-hq/element-call.
    """
    ref = parse_image(image)
    sources = [Source("upstream", ref.repository)]
    registries = [(ref.registry, ref.path)]
    for alternate in alternates:
        registry, path = parse_repository(str(alternate))
        sources.append(Source("alternate", f"{registry}/{path}"))
        registries.append((registry, path))
    for registry, path in registries:
        for mirror in (mirrors or {}).get(registry, []) or []:
            mirror = str(mirror).strip().rstrip("/")
            if mirror:
                sources.append(Source("mirror", f"{mirror}/{path}"))
    seen: set[str] = set()
    unique = []
    for source in sources:
        if source.repository not in seen:
            seen.add(source.repository)
            unique.append(source)
    return unique


Runner = Callable[..., subprocess.CompletedProcess]


def skopeo_raw_manifest(
    reference: str,
    *,
    timeout: int = 60,
    attempts: int = 3,
    backoff: float = 3.0,
    run: Runner = subprocess.run,
) -> bytes:
    """Fetch the raw manifest (index) bytes with retries."""
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            result = run(
                ["skopeo", "inspect", "--raw", f"docker://{reference}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RegistryError("skopeo is required; run bootstrap.sh first") from exc
        except subprocess.TimeoutExpired:
            last_error = f"timed out after {timeout}s"
        else:
            if result.returncode == 0 and result.stdout:
                return bytes(result.stdout)
            stderr = result.stderr.decode("utf-8", "replace") if isinstance(result.stderr, bytes) else str(result.stderr)
            last_error = stderr.strip()[-300:] or f"exit code {result.returncode}"
        if attempt < attempts:
            time.sleep(backoff * attempt)
    raise RegistryError(f"{reference}: {last_error}")


def manifest_digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def manifest_platforms(raw: bytes) -> set[tuple[str, str]] | None:
    """Platforms of a manifest index, or None for a single-platform manifest."""
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RegistryError(f"invalid manifest JSON: {exc}") from exc
    if data.get("mediaType") in INDEX_MEDIA_TYPES or "manifests" in data:
        return {
            (str(m.get("platform", {}).get("os")), str(m.get("platform", {}).get("architecture")))
            for m in data.get("manifests", [])
            if isinstance(m, dict)
        }
    return None


@dataclass(frozen=True)
class Located:
    digest: str
    source: Source
    platforms: set[tuple[str, str]] | None


def locate_digest(
    image: str,
    sources: list[Source],
    *,
    fetch: Callable[[str], bytes] = skopeo_raw_manifest,
    log: Callable[[str], None] = lambda message: None,
) -> Located:
    """Resolve the manifest digest of `image`'s tag from a trusted source.

    Upstream and official alternate registries are trusted. When none of them
    is reachable, public mirrors are only trusted if at least two independent
    mirrors return the same digest.
    """
    tag = parse_image(image).tag
    errors: list[str] = []
    mirror_hits: dict[str, list[Located]] = {}
    for source in sources:
        try:
            raw = fetch(source.tagged(tag))
        except RegistryError as exc:
            errors.append(f"{source.kind} {source.repository}: {exc}")
            log(f"  unreachable: {source.tagged(tag)}")
            continue
        located = Located(manifest_digest(raw), source, manifest_platforms(raw))
        if source.kind != "mirror":
            return located
        mirror_hits.setdefault(located.digest, []).append(located)
        if len(mirror_hits[located.digest]) >= 2:
            log(f"  upstream unreachable; digest confirmed by two mirrors for {image}")
            return located
    if mirror_hits:
        found = ", ".join(
            f"{digest[:19]} via {hits[0].source.repository}" for digest, hits in mirror_hits.items()
        )
        raise RegistryError(
            f"{image}: upstream registries unreachable and fewer than two mirrors agree on "
            f"the digest ({found}); refusing to trust a single mirror. Configure "
            "matrix_registry_proxy or retry when upstream is reachable. Errors: "
            + "; ".join(errors)
        )
    raise RegistryError(f"{image}: no source reachable. " + "; ".join(errors))

#!/usr/bin/env python3
"""Offline tests for registry fallback: source ordering, digest trust rules
and the digest-pinned image puller (docker is faked)."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import pull_images as pi  # noqa: E402
import registry  # noqa: E402

MIRRORS = {
    "docker.io": ["mirror.gcr.io", "dockerhub.timeweb.cloud"],
    "ghcr.io": ["ghcr.nju.edu.cn", "ghcr.m.daocloud.io"],
}
DIGEST = "sha256:" + "d" * 64


class ParseAndSourcesTest(unittest.TestCase):
    def test_parse_image(self):
        self.assertEqual(registry.parse_image("postgres:18.6").canonical, "docker.io/library/postgres:18.6")
        self.assertEqual(registry.parse_image("matrixdotorg/synapse:v1.161.0").repository, "docker.io/matrixdotorg/synapse")
        ref = registry.parse_image("ghcr.io/element-hq/element-call:v0.26.0")
        self.assertEqual((ref.registry, ref.path, ref.tag), ("ghcr.io", "element-hq/element-call", "v0.26.0"))
        with self.assertRaises(registry.RegistryError):
            registry.parse_image("ghcr.io/element-hq/element-call")

    def test_candidate_order_upstream_alternate_mirrors(self):
        sources = registry.candidate_sources(
            "ghcr.io/etkecc/ketesa:v1.5.0", ["docker.io/etkecc/ketesa"], MIRRORS
        )
        self.assertEqual(
            [(s.kind, s.repository) for s in sources],
            [
                ("upstream", "ghcr.io/etkecc/ketesa"),
                ("alternate", "docker.io/etkecc/ketesa"),
                ("mirror", "ghcr.nju.edu.cn/etkecc/ketesa"),
                ("mirror", "ghcr.m.daocloud.io/etkecc/ketesa"),
                ("mirror", "mirror.gcr.io/etkecc/ketesa"),
                ("mirror", "dockerhub.timeweb.cloud/etkecc/ketesa"),
            ],
        )

    def test_docker_hub_official_images_keep_library_prefix_on_mirrors(self):
        sources = registry.candidate_sources("postgres:18.6", [], MIRRORS)
        self.assertIn("mirror.gcr.io/library/postgres", [s.repository for s in sources])


class LocateDigestTest(unittest.TestCase):
    def locate(self, responses, image="ghcr.io/element-hq/element-call:v0.26.0", alternates=()):
        def fetch(reference):
            value = responses.get(reference.rsplit(":", 1)[0])
            if value is None:
                raise registry.RegistryError(f"{reference}: dial tcp: i/o timeout")
            return value
        sources = registry.candidate_sources(image, alternates, MIRRORS)
        return registry.locate_digest(image, sources, fetch=fetch)

    def test_upstream_is_trusted(self):
        found = self.locate({"ghcr.io/element-hq/element-call": b"{}"})
        self.assertEqual(found.source.kind, "upstream")

    def test_official_alternate_is_trusted_when_upstream_down(self):
        found = self.locate({"docker.io/etkecc/ketesa": b"{}"},
                            image="ghcr.io/etkecc/ketesa:v1.5.0",
                            alternates=["docker.io/etkecc/ketesa"])
        self.assertEqual(found.source.kind, "alternate")

    def test_single_mirror_is_not_trusted(self):
        with self.assertRaisesRegex(registry.RegistryError, "fewer than two mirrors agree"):
            self.locate({"ghcr.nju.edu.cn/element-hq/element-call": b"{}"})

    def test_two_agreeing_mirrors_are_trusted(self):
        found = self.locate({
            "ghcr.nju.edu.cn/element-hq/element-call": b"{}",
            "ghcr.m.daocloud.io/element-hq/element-call": b"{}",
        })
        self.assertEqual(found.digest, registry.manifest_digest(b"{}"))

    def test_disagreeing_mirrors_are_rejected(self):
        with self.assertRaises(registry.RegistryError):
            self.locate({
                "ghcr.nju.edu.cn/element-hq/element-call": b'{"a":1}',
                "ghcr.m.daocloud.io/element-hq/element-call": b'{"b":2}',
            })

    def test_skopeo_retries_then_succeeds(self):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) < 3:
                raise subprocess.TimeoutExpired(cmd, 1)
            return subprocess.CompletedProcess(cmd, 0, b"{}", b"")

        with mock.patch.object(registry.time, "sleep"):
            self.assertEqual(registry.skopeo_raw_manifest("ghcr.io/x/y:1", run=run), b"{}")
        self.assertEqual(len(calls), 3)


class FakeDocker:
    """Minimal docker CLI: images keyed by reference, pulls per reachable repo."""

    def __init__(self, reachable, present=None):
        self.reachable = set(reachable)
        self.images = dict(present or {})  # reference -> RepoDigests
        self.calls = []

    def __call__(self, *args, timeout=None):
        self.calls.append(args)
        if args[:2] == ("image", "inspect"):
            ref = args[2]
            if ref not in self.images:
                return subprocess.CompletedProcess(args, 1, "", "No such image")
            return subprocess.CompletedProcess(args, 0, json.dumps(self.images[ref]), "")
        if args[0] == "pull":
            ref = args[-1]
            repo = ref.split("@")[0] if "@" in ref else ref.rsplit(":", 1)[0]
            if repo not in self.reachable:
                raise subprocess.TimeoutExpired(args, timeout or 1)
            self.images[ref] = [ref] if "@" in ref else []
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "tag":
            self.images[args[2]] = list(self.images.get(args[1], []))
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)


class PullImagesTest(unittest.TestCase):
    item = {"image": "ghcr.io/element-hq/element-call:v0.26.0", "digest": DIGEST}

    def run_pull(self, docker, item=None, policy_source=None):
        with mock.patch.object(pi, "docker", docker), mock.patch.object(pi.time, "sleep"), \
             contextlib.redirect_stdout(io.StringIO()):
            return pi.pull_component("element_call", item or self.item, policy_source or {}, MIRRORS, "amd64", 5)

    def test_falls_back_to_mirror_and_tags_canonical_reference(self):
        docker = FakeDocker(reachable={"ghcr.m.daocloud.io/element-hq/element-call"})
        result = self.run_pull(docker)
        self.assertIn("from mirror ghcr.m.daocloud.io/element-hq/element-call", result)
        self.assertIn(("tag", f"ghcr.m.daocloud.io/element-hq/element-call@{DIGEST}",
                       "ghcr.io/element-hq/element-call:v0.26.0"), docker.calls)
        pulls = [c[-1] for c in docker.calls if c[0] == "pull"]
        self.assertTrue(all(ref.endswith(f"@{DIGEST}") for ref in pulls), pulls)

    def test_upstream_first(self):
        docker = FakeDocker(reachable={"ghcr.io/element-hq/element-call", "ghcr.nju.edu.cn/element-hq/element-call"})
        self.assertIn("from upstream", self.run_pull(docker))

    def test_present_with_locked_digest_is_skipped(self):
        docker = FakeDocker(reachable=set(), present={
            "ghcr.io/element-hq/element-call:v0.26.0": [f"ghcr.io/element-hq/element-call@{DIGEST}"],
        })
        self.assertTrue(self.run_pull(docker).startswith("PRESENT"))
        self.assertFalse([c for c in docker.calls if c[0] == "pull"])

    def test_present_with_other_digest_is_repulled(self):
        docker = FakeDocker(
            reachable={"ghcr.io/element-hq/element-call"},
            present={"ghcr.io/element-hq/element-call:v0.26.0": ["ghcr.io/element-hq/element-call@sha256:" + "0" * 64]},
        )
        self.assertTrue(self.run_pull(docker).startswith("PULLED"))

    def test_without_digest_mirrors_are_never_used(self):
        docker = FakeDocker(reachable={"ghcr.nju.edu.cn/element-hq/element-call"})
        with self.assertRaisesRegex(pi.PullError, "mirrors were not used"):
            self.run_pull(docker, item={"image": "ghcr.io/element-hq/element-call:v0.26.0"})
        self.assertFalse([c for c in docker.calls if c[0] == "pull" and "nju" in c[-1]])

    def test_all_sources_down_fails_with_summary(self):
        with self.assertRaisesRegex(pi.PullError, "could not be pulled from any source"):
            self.run_pull(FakeDocker(reachable=set()))


if __name__ == "__main__":
    unittest.main()

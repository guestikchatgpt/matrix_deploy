#!/usr/bin/env python3
"""Offline unit tests for tools/resolve_versions.py minimum-version handling.

Network access (GitHub, Docker Hub, skopeo) is replaced by fakes, so these
tests only exercise the resolver's own decision logic.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import resolve_versions as rv  # noqa: E402

POLICY_PATH = ROOT / "ansible/inventory/group_vars/all/versions.yml"
MULTIARCH_INDEX = json.dumps({
    "mediaType": "application/vnd.oci.image.index.v1+json",
    "manifests": [
        {"digest": "sha256:" + "a" * 64, "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": "sha256:" + "b" * 64, "platform": {"os": "linux", "architecture": "arm64"}},
    ],
}).encode()
INDEX_DIGEST = "sha256:" + hashlib.sha256(MULTIARCH_INDEX).hexdigest()


def load_policy() -> dict:
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))


def floor_versions(policy: dict) -> dict[str, str]:
    return {
        name: str(source["min_version"])
        for name, source in policy["matrix_version_sources"].items()
    }


class FakeUpstream:
    """Serves one release tag per GitHub repo and PostgreSQL metadata."""

    def __init__(self, policy: dict, versions: dict[str, str]):
        self.by_repo = {
            source["github_repo"]: versions[name]
            for name, source in policy["matrix_version_sources"].items()
            if source.get("resolver") == "github_release"
        }
        self.postgres = versions["postgresql"]

    def github_latest_release(self, repo, release_regex):
        return self.by_repo[repo], f"https://example.invalid/{repo}"

    def http_request(self, url, *, github=False):
        return f"Tags: {self.postgres}, 18, latest\nTags: 19beta4\n".encode()


class ResolveVersionsTest(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()
        self.floors = floor_versions(self.policy)
        self.fetched: list[str] = []

        def fake_fetch(reference: str) -> bytes:
            self.fetched.append(reference)
            return MULTIARCH_INDEX

        patches = [
            mock.patch.object(rv, "dockerhub_tag", return_value={}),
            mock.patch.object(rv, "skopeo_verify", return_value=None),
            mock.patch.object(rv, "fetch_manifest", side_effect=fake_fetch),
            mock.patch.object(rv.time, "sleep", return_value=None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def resolve_with(self, versions):
        fake = FakeUpstream(self.policy, versions)
        with mock.patch.object(rv, "github_latest_release", fake.github_latest_release), \
             mock.patch.object(rv, "http_request", fake.http_request):
            return rv.resolve(self.policy, "amd64")

    def test_version_tuple(self):
        self.assertEqual(rv.version_tuple("v1.161.0"), (1, 161, 0))
        self.assertEqual(rv.version_tuple("0.7.0"), (0, 7, 0))
        self.assertEqual(rv.version_tuple("18.6"), (18, 6))
        self.assertTrue(rv.below_minimum("v1.12.9", "v1.12.29"))
        self.assertFalse(rv.below_minimum("v1.13.0", "v1.12.29"))
        with self.assertRaises(rv.ResolveError):
            rv.version_tuple("v1.0.0-rc.1")

    def test_every_source_has_a_floor(self):
        self.assertEqual(set(self.floors), set(self.policy["matrix_version_sources"]))

    def test_resolve_accepts_floor_versions(self):
        lock = self.resolve_with(self.floors)
        components = lock["matrix_version_lock"]["components"]
        self.assertEqual(rv.minimum_violations(self.policy, components), [])
        self.assertEqual(lock["element_jwt_image"], "ghcr.io/element-hq/lk-jwt-service:0.7.0")
        self.assertEqual(lock["postgresql_image"], "postgres:18.6")

    def test_resolve_rejects_upstream_older_than_floor(self):
        versions = dict(self.floors, element_call="v0.25.0")
        with self.assertRaisesRegex(rv.ResolveError, "element_call.*older than the verified minimum"):
            self.resolve_with(versions)

    def test_check_mode_fails_closed_on_old_lock(self):
        lock = self.resolve_with(self.floors)
        lock["matrix_version_lock"]["components"]["synapse"]["version"] = "v1.160.0"
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = pathlib.Path(tmp) / "versions.yml"
            lock_path.write_text(yaml.safe_dump(lock), encoding="utf-8")
            argv = ["resolve_versions.py", "--policy", str(POLICY_PATH),
                    "--lock", str(lock_path), "--check", "--arch", "amd64"]
            with mock.patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(rv.ResolveError, r"synapse: v1\.160\.0 < required v1\.161\.0.*upgrade\.sh"):
                    rv.main()

    def test_check_mode_accepts_current_lock_and_keeps_it(self):
        lock = self.resolve_with(self.floors)
        fake = FakeUpstream(self.policy, self.floors)
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = pathlib.Path(tmp) / "versions.yml"
            lock_path.write_text(yaml.safe_dump(lock), encoding="utf-8")
            before = lock_path.read_bytes()
            argv = ["resolve_versions.py", "--policy", str(POLICY_PATH),
                    "--lock", str(lock_path), "--check", "--arch", "amd64"]
            out = io.StringIO()
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(rv, "github_latest_release", fake.github_latest_release), \
                 mock.patch.object(rv, "http_request", fake.http_request), \
                 contextlib.redirect_stdout(out):
                self.assertEqual(rv.main(), 0)
            self.assertIn("Locked versions are current", out.getvalue())
            self.assertEqual(lock_path.read_bytes(), before)

    def test_resolve_pins_every_image_to_a_digest(self):
        lock = self.resolve_with(self.floors)
        for name, item in lock["matrix_version_lock"]["components"].items():
            self.assertEqual(item["digest"], INDEX_DIGEST, name)
            self.assertTrue(item["digest_source"])
        self.assertIn("ghcr.io/element-hq/element-call:v0.26.0", self.fetched)

    def test_github_api_outage_falls_back_to_registry_tags(self):
        fake = FakeUpstream(self.policy, self.floors)

        def github_down(repo, regex):
            raise rv.ResolveError("request failed for https://api.github.com: timed out")

        registry_tags = {
            "ghcr.io/element-hq/lk-jwt-service": ["0.6.0", "0.7.0", "0.8.0-rc1", "latest"],
        }

        def list_tags(repository, **kwargs):
            if repository in registry_tags:
                return registry_tags[repository]
            raise rv.ResolveError(f"cannot list tags of {repository}")

        policy = dict(self.policy)
        policy["matrix_version_sources"] = {
            "element_jwt": self.policy["matrix_version_sources"]["element_jwt"],
        }
        with mock.patch.object(rv, "github_latest_release", github_down), \
             mock.patch.object(rv, "registry_list_tags", list_tags), \
             mock.patch.object(rv, "http_request", fake.http_request), \
             contextlib.redirect_stdout(io.StringIO()):
            lock = rv.resolve(policy, "amd64")
        item = lock["matrix_version_lock"]["components"]["element_jwt"]
        self.assertEqual(item["version"], "v0.7.0")  # rc tag ignored
        self.assertEqual(item["image"], "ghcr.io/element-hq/lk-jwt-service:0.7.0")

    def test_check_mode_adds_digests_to_old_lock_without_version_change(self):
        lock = self.resolve_with(self.floors)
        for item in lock["matrix_version_lock"]["components"].values():
            item.pop("digest")
            item.pop("digest_source")
        fake = FakeUpstream(self.policy, self.floors)
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = pathlib.Path(tmp) / "versions.yml"
            lock_path.write_text(yaml.safe_dump(lock), encoding="utf-8")
            argv = ["resolve_versions.py", "--policy", str(POLICY_PATH),
                    "--lock", str(lock_path), "--check", "--arch", "amd64"]
            out = io.StringIO()
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(rv, "github_latest_release", fake.github_latest_release), \
                 mock.patch.object(rv, "http_request", fake.http_request), \
                 contextlib.redirect_stdout(out):
                self.assertEqual(rv.main(), 0)
            self.assertIn("LOCK_DIGESTS_ADDED", out.getvalue())
            updated = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
            for name, item in updated["matrix_version_lock"]["components"].items():
                self.assertEqual(item["digest"], INDEX_DIGEST, name)
                self.assertEqual(item["version"], lock["matrix_version_lock"]["components"][name]["version"])

    def test_missing_platform_is_rejected(self):
        single_arch = json.dumps({
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{"digest": "sha256:" + "c" * 64,
                           "platform": {"os": "linux", "architecture": "amd64"}}],
        }).encode()
        with mock.patch.object(rv, "fetch_manifest", return_value=single_arch):
            with self.assertRaisesRegex(rv.ResolveError, "no linux/arm64"):
                fake = FakeUpstream(self.policy, self.floors)
                with mock.patch.object(rv, "github_latest_release", fake.github_latest_release), \
                     mock.patch.object(rv, "http_request", fake.http_request), \
                     contextlib.redirect_stdout(io.StringIO()):
                    rv.resolve(self.policy, "arm64")


if __name__ == "__main__":
    unittest.main()

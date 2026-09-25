#!/usr/bin/env python3
"""Offline unit tests for tools/resolve_versions.py minimum-version handling.

Network access (GitHub, Docker Hub, skopeo) is replaced by fakes, so these
tests only exercise the resolver's own decision logic.
"""
from __future__ import annotations

import contextlib
import io
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
        patches = [
            mock.patch.object(rv, "dockerhub_tag", return_value={}),
            mock.patch.object(rv, "skopeo_verify", return_value=None),
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


if __name__ == "__main__":
    unittest.main()

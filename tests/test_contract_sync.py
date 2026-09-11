from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SSH_ROOT = ROOT.parent / "rofi-ssh-plus"
TMUX_ROOT = ROOT.parent / "rofi-tmux-plus"
HOST_BUNDLE = "contracts/host-mesh-v1"
TMUX_BUNDLE = "contracts/tmux-session-v1"


@unittest.skipUnless(
    SSH_ROOT.is_dir() and TMUX_ROOT.is_dir(),
    "sibling producer checkouts are unavailable",
)
class ContractSyncTests(unittest.TestCase):
    def copies(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        ssh = root / "ssh"
        tmux = root / "tmux"
        agent = root / "agent"
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(SSH_ROOT, ssh, ignore=ignore)
        shutil.copytree(TMUX_ROOT, tmux, ignore=ignore)
        shutil.copytree(ROOT, agent, ignore=ignore)
        return temporary, ssh, tmux, agent

    @staticmethod
    def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def commit(self, root: Path, *paths: str) -> None:
        self.git(root, "add", *paths)
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_AUTHOR_NAME": "contract-sync-test",
                "GIT_AUTHOR_EMAIL": "contract-sync-test@example.invalid",
                "GIT_COMMITTER_NAME": "contract-sync-test",
                "GIT_COMMITTER_EMAIL": "contract-sync-test@example.invalid",
            }
        )
        subprocess.run(
            ["git", "-C", str(root), "commit", "-m", "test: advance producer"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )

    @staticmethod
    def rewrite_manifest(bundle: Path) -> None:
        files = sorted(
            (
                path.relative_to(bundle).as_posix()
                for path in bundle.rglob("*")
                if path.is_file() and path.name not in {"SHA256SUMS", "SOURCE.json"}
            ),
            key=lambda name: name.encode(),
        )
        manifest = "".join(
            f"{hashlib.sha256((bundle / name).read_bytes()).hexdigest()}  {name}\n"
            for name in files
        )
        (bundle / "SHA256SUMS").write_text(manifest, encoding="utf-8")

    @staticmethod
    def run_sync(ssh: Path, tmux: Path, agent: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(agent / "scripts/check-contract-sync"),
                str(ssh),
                str(tmux),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_later_non_contract_producer_commits_are_accepted(self) -> None:
        temporary, ssh, tmux, agent = self.copies()
        with temporary:
            with (ssh / "README.md").open("a", encoding="utf-8") as stream:
                stream.write("\nfuture Host Mesh implementation note\n")
            with (tmux / "README.md").open("a", encoding="utf-8") as stream:
                stream.write("\nfuture Tmux implementation note\n")
            self.commit(ssh, "README.md")
            self.commit(tmux, "README.md")
            result = self.run_sync(ssh, tmux, agent)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("remains unchanged"), 2)

    def test_non_ancestor_producer_commit_is_rejected(self) -> None:
        temporary, ssh, tmux, agent = self.copies()
        with temporary:
            tree = self.git(ssh, "rev-parse", "HEAD^{tree}").stdout.strip()
            environment = os.environ.copy()
            environment.update(
                {
                    "GIT_AUTHOR_NAME": "contract-sync-test",
                    "GIT_AUTHOR_EMAIL": "contract-sync-test@example.invalid",
                    "GIT_COMMITTER_NAME": "contract-sync-test",
                    "GIT_COMMITTER_EMAIL": "contract-sync-test@example.invalid",
                }
            )
            commit = subprocess.run(
                ["git", "-C", str(ssh), "commit-tree", tree, "-m", "test: diverge producer"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            ).stdout.strip()
            self.git(ssh, "update-ref", "refs/heads/main", commit)
            result = self.run_sync(ssh, tmux, agent)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not descend", result.stderr)

    def test_historical_contract_manifest_change_is_rejected_after_restore(self) -> None:
        temporary, ssh, tmux, agent = self.copies()
        with temporary:
            bundle = ssh / HOST_BUNDLE
            contract = bundle / "contract.md"
            canonical_contract = contract.read_bytes()
            canonical_manifest = (bundle / "SHA256SUMS").read_bytes()
            with contract.open("a", encoding="utf-8") as stream:
                stream.write("\ncontract change\n")
            self.rewrite_manifest(bundle)
            self.commit(ssh, f"{HOST_BUNDLE}/contract.md", f"{HOST_BUNDLE}/SHA256SUMS")

            ancestor = self.git(ssh, "rev-parse", "HEAD").stdout.strip()
            provenance_path = agent / HOST_BUNDLE / "SOURCE.json"
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            released_digest = provenance["bundleDigest"]
            provenance["sourceCommit"] = ancestor
            provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

            contract.write_bytes(canonical_contract)
            (bundle / "SHA256SUMS").write_bytes(canonical_manifest)
            self.commit(ssh, f"{HOST_BUNDLE}/contract.md", f"{HOST_BUNDLE}/SHA256SUMS")

            self.assertEqual((bundle / "SHA256SUMS").read_bytes(), canonical_manifest)
            self.assertEqual(
                json.loads(provenance_path.read_text(encoding="utf-8"))["bundleDigest"],
                released_digest,
            )
            result = self.run_sync(ssh, tmux, agent)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum manifest changed after released provenance", result.stderr)

    def test_dirty_current_contract_bundle_is_rejected(self) -> None:
        temporary, ssh, tmux, agent = self.copies()
        with temporary:
            contract = tmux / TMUX_BUNDLE / "contract.md"
            with contract.open("a", encoding="utf-8") as stream:
                stream.write("\nuncommitted contract change\n")
            result = self.run_sync(ssh, tmux, agent)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()

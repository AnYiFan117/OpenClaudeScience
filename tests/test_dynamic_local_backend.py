"""Smoke tests for the simplified DynamicLocalShellBackend passthrough."""

import os
import tempfile
import unittest
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from internagents.dynamic_local_backend import DynamicLocalShellBackend


class DynamicLocalShellBackendPassthroughTest(unittest.TestCase):
    def _backend(
        self,
        workspace: Path,
        *,
        active_skills_path: Path | None = None,
    ) -> DynamicLocalShellBackend:
        return DynamicLocalShellBackend(
            resource_id="local",
            fallback_root=workspace,
            workspace_override=str(workspace),
            active_skills_path=active_skills_path,
        )

    def test_workspace_absolute_path_reads_actual_file(self) -> None:
        """A real absolute path pointing inside the workspace must resolve to
        the actual file — no `mg/mnt/...` nested-dir pollution like the
        removed translator produced."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "output").mkdir()
            target = root / "output" / "hello.txt"
            target.write_text("hi\n", encoding="utf-8")

            backend = self._backend(root)
            result = backend.read(str(target))

        self.assertIsNone(result.error, f"Unexpected error: {result.error}")
        self.assertIsNotNone(result.file_data)
        self.assertIn("hi", result.file_data["content"])

    def test_shell_execute_uses_workspace_as_cwd(self) -> None:
        """Shell commands should start with the workspace as CWD, so `pwd`
        prints the workspace directory (not something virtual)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            backend = self._backend(root)
            result = backend.execute("pwd")

        self.assertIn(str(root), result.output or "")

    def test_active_skills_prepended_to_pythonpath(self) -> None:
        """When `active_skills_path` holds a valid skill dir, the shell env's
        PYTHONPATH should be prefixed with it so `import <module>` works."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            active_skills = root / ".internagents" / "active-skills"
            active_skills.mkdir(parents=True)

            skill_dir = active_skills / "demo-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo-skill\n---\n", encoding="utf-8"
            )
            (skill_dir / "kernel.py").write_text(
                "MAGIC = 'skill_visible'\n", encoding="utf-8"
            )

            backend = self._backend(root, active_skills_path=active_skills)
            result = backend.execute("python3 -c 'import kernel; print(kernel.MAGIC)'")

        self.assertIn("skill_visible", result.output or "")

    def test_no_forbidden_workspace_pollution(self) -> None:
        """After executing a command that references an absolute host path
        inside the workspace, the workspace must NOT contain the mirrored
        `mnt/...` / `root/...` tree that the old translator created."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "output").mkdir()
            (root / "output" / "x.txt").write_text("x", encoding="utf-8")

            backend = self._backend(root)
            backend.execute(f"cat {root / 'output' / 'x.txt'}")

            # The workspace root should have exactly one child ("output"),
            # not a mirrored `<parts of the absolute path>` tree.
            children = {p.name for p in root.iterdir()}
        self.assertEqual(children, {"output"})


if __name__ == "__main__":
    unittest.main()

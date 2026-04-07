from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import setup_support as ss


class SetupSupportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def make_source_skill_dir(self) -> Path:
        skill_dir = self.root / "source" / "skill-youtrack"
        (skill_dir / "agents").mkdir(parents=True, exist_ok=True)
        (skill_dir / "locales").mkdir(parents=True, exist_ok=True)
        (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (skill_dir / ".git").mkdir(parents=True, exist_ok=True)
        (skill_dir / ".venv").mkdir(parents=True, exist_ok=True)
        (skill_dir / "__pycache__").mkdir(parents=True, exist_ok=True)
        (skill_dir / "tests").mkdir(parents=True, exist_ok=True)

        (skill_dir / "README.md").write_text("readme\n", encoding="utf-8")
        (skill_dir / "Makefile").write_text(".PHONY: skill\nskill:\n\t@true\n", encoding="utf-8")
        (skill_dir / "agents" / "runtime.json").write_text(
            '{"commands": {"yt": "scripts/yt", "ytx": "scripts/ytx"}}\n',
            encoding="utf-8",
        )
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: skill-youtrack\n"
            "description: >\n"
            "  English source description\n"
            "  on multiple lines\n"
            "triggers:\n"
            '  - "youtrack"\n'
            '  - "youtrack issue"\n'
            "---\n\n"
            "# Sample Skill\n",
            encoding="utf-8",
        )
        (skill_dir / "agents" / "openai.yaml").write_text(
            'interface:\n'
            '  display_name: "English Display"\n'
            '  short_description: "English Short"\n'
            '  default_prompt: "Use $skill-youtrack in English."\n',
            encoding="utf-8",
        )
        (skill_dir / "locales" / "metadata.json").write_text(
            json.dumps(
                {
                    "locales": {
                        "en": {
                            "description": "English localized description",
                            "display_name": "English Display",
                            "short_description": "English Short",
                            "default_prompt": "Use $skill-youtrack in English.",
                            "local_prefix": "[local] ",
                        },
                        "ru": {
                            "description": "Русское описание",
                            "display_name": "Русский Display",
                            "short_description": "Русский Short",
                            "default_prompt": "Используй $skill-youtrack по-русски.",
                            "local_prefix": "[локально] ",
                        },
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (skill_dir / ".skill_triggers").mkdir(parents=True, exist_ok=True)
        (skill_dir / ".skill_triggers" / "en.md").write_text(
            "- youtrack\n- my tasks in youtrack\n- youtrack board\n- youtrack sprint\n- create youtrack issue\n- update youtrack issue\n- youtrack issue comments\n",
            encoding="utf-8",
        )
        (skill_dir / ".skill_triggers" / "ru.md").write_text(
            "- ютрек\n- задачи в youtrack\n- доска youtrack\n- задачи в спринте youtrack\n- создать задачу в youtrack\n- обновить задачу в youtrack\n- комментарии в youtrack\n",
            encoding="utf-8",
        )
        (skill_dir / ".git" / "config").write_text("", encoding="utf-8")
        (skill_dir / ".venv" / "marker").write_text("", encoding="utf-8")
        (skill_dir / "__pycache__" / "cache.pyc").write_text("", encoding="utf-8")
        (skill_dir / "tests" / "test_dummy.py").write_text("pass\n", encoding="utf-8")
        return skill_dir

    def test_render_skill_metadata_dual_mode_merges_trigger_lists(self) -> None:
        skill_dir = self.make_source_skill_dir()

        ss.render_skill_metadata(skill_dir, "ru-en", "local")

        skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        openai_yaml = (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertIn(
            'description: "Русское описание Триггеры: \\"ютрек\\", \\"задачи в youtrack\\", \\"доска youtrack\\", \\"задачи в спринте youtrack\\", \\"создать задачу в youtrack\\", \\"обновить задачу в youtrack\\". / English localized description Triggers: \\"youtrack\\", \\"my tasks in youtrack\\", \\"youtrack board\\", \\"youtrack sprint\\", \\"create youtrack issue\\", \\"update youtrack issue\\"."',
            skill_text,
        )
        self.assertIn('  - "ютрек"\n', skill_text)
        self.assertIn('  - "доска youtrack"\n', skill_text)
        self.assertIn('  - "youtrack"\n', skill_text)
        self.assertIn('  - "my tasks in youtrack"\n', skill_text)
        self.assertIn('display_name: "[локально] Русский Display"', openai_yaml)
        self.assertIn('short_description: "[локально] Русский Short"', openai_yaml)

    def test_perform_local_install_requires_locale_for_first_install(self) -> None:
        source_dir = self.make_source_skill_dir()
        repo_root = self.root / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)

        with mock.patch.object(ss, "resolve_repo_root", return_value=repo_root.resolve()):
            with self.assertRaises(ss.SetupError) as exc:
                ss.perform_install(
                    source_dir=source_dir,
                    install_mode="local",
                    requested_locale=None,
                    repo_root=repo_root,
                )

        self.assertIn("First local install requires --locale", str(exc.exception))

    def test_perform_local_install_uses_project_fixed_locale(self) -> None:
        source_dir = self.make_source_skill_dir()
        repo_root = self.root / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)

        with mock.patch.object(ss, "resolve_repo_root", return_value=repo_root.resolve()):
            first_result = ss.perform_install(
                source_dir=source_dir,
                install_mode="local",
                requested_locale="ru",
                repo_root=repo_root,
            )

            with self.assertRaises(ss.SetupError) as exc:
                ss.perform_install(
                    source_dir=source_dir,
                    install_mode="local",
                    requested_locale="en",
                    repo_root=repo_root,
                )

        self.assertEqual(
            first_result.runtime_dir.resolve(),
            (repo_root / ".agents" / "skills" / "skill-youtrack").resolve(),
        )
        self.assertIn("project-fixed", str(exc.exception))
        self.assertFalse((repo_root / ".claude" / "skills" / "skill-youtrack").exists())
        self.assertFalse((repo_root / ".codex" / "skills" / "skill-youtrack").exists())

    def test_perform_local_install_creates_committed_safe_runtime_copy(self) -> None:
        source_dir = self.make_source_skill_dir()
        repo_root = self.root / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)

        with mock.patch.object(ss, "resolve_repo_root", return_value=repo_root.resolve()):
            result = ss.perform_install(
                source_dir=source_dir,
                install_mode="local",
                requested_locale="ru",
                repo_root=repo_root,
            )

        self.assertTrue((result.runtime_dir / "Makefile").exists())
        self.assertTrue((result.runtime_dir / "agents" / "runtime.json").exists())
        self.assertFalse((result.runtime_dir / "README.md").exists())
        self.assertFalse((result.runtime_dir / "locales" / "metadata.json").exists())
        self.assertFalse((result.runtime_dir / "scripts" / "setup_main.py").exists())
        self.assertFalse((result.runtime_dir / "scripts" / "setup_support.py").exists())
        self.assertFalse((result.runtime_dir / "tests").exists())
        self.assertTrue((result.runtime_dir / ".skill_triggers" / "en.md").exists())
        self.assertTrue((result.runtime_dir / ".skill_triggers" / "ru.md").exists())
        manifest = json.loads((result.runtime_dir / ss.MANIFEST_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 2)
        self.assertNotIn("source_dir", manifest)
        self.assertNotIn("runtime_dir", manifest)

    def test_render_skill_metadata_uses_markdown_triggers_as_single_source(self) -> None:
        source_dir = self.make_source_skill_dir()

        ss.render_skill_metadata(source_dir, "en", "local")

        skill_text = (source_dir / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('  - "youtrack"', skill_text)
        self.assertIn('  - "youtrack issue comments"', skill_text)
        self.assertIn(
            'description: "English localized description Triggers: \\"youtrack\\", \\"my tasks in youtrack\\", \\"youtrack board\\", \\"youtrack sprint\\", \\"create youtrack issue\\", \\"update youtrack issue\\"."',
            skill_text,
        )
        self.assertNotIn('[local] English localized description', skill_text)

    def test_load_metadata_catalog_rejects_trigger_lists_in_metadata_json(self) -> None:
        source_dir = self.make_source_skill_dir()
        metadata_path = source_dir / "locales" / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["locales"]["en"]["triggers"] = ["legacy trigger"]
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        with self.assertRaises(ss.SetupError) as exc:
            ss.load_metadata_catalog(source_dir)

        self.assertIn("must define triggers only in", str(exc.exception))

    def test_resolve_source_dir_prefers_manifest_source_dir(self) -> None:
        source_dir = self.make_source_skill_dir().resolve()
        installed_dir = self.root / "installed" / "skill-youtrack"
        installed_dir.mkdir(parents=True, exist_ok=True)
        ss.write_install_manifest(
            skill_dir=installed_dir,
            skill_name="skill-youtrack",
            install_mode="local",
            locale_mode="en",
            source_dir=source_dir,
        )

        self.assertEqual(ss.resolve_source_dir(installed_dir), source_dir)


if __name__ == "__main__":
    unittest.main()

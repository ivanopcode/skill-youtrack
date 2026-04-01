from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import setup_support as ss


class SetupSupportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.xdg_data_home = self.root / "xdg-data"
        self.xdg_config_home = self.root / "xdg-config"
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "XDG_DATA_HOME": str(self.xdg_data_home),
                "XDG_CONFIG_HOME": str(self.xdg_config_home),
            },
            clear=False,
        )
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

    def make_source_skill_dir(self) -> Path:
        skill_dir = self.root / "source" / "skill-youtrack"
        (skill_dir / "agents").mkdir(parents=True, exist_ok=True)
        (skill_dir / "locales").mkdir(parents=True, exist_ok=True)
        (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: skill-youtrack\n"
            "description: English source description\n"
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
        (skill_dir / "scripts" / "bootstrap.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        return skill_dir

    def test_render_skill_metadata_dual_mode_only_bilingualizes_description(self) -> None:
        skill_dir = self.make_source_skill_dir()

        ss.render_skill_metadata(skill_dir, "ru-en", "global")

        skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        openai_yaml = (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertIn('description: "Русское описание / English localized description"', skill_text)
        self.assertIn('display_name: "Русский Display"', openai_yaml)
        self.assertIn('short_description: "Русский Short"', openai_yaml)
        self.assertIn('default_prompt: "Используй $skill-youtrack по-русски."', openai_yaml)

    def test_perform_global_install_requires_locale_for_first_install(self) -> None:
        source_dir = self.make_source_skill_dir()

        with self.assertRaises(ss.SetupError) as exc:
            ss.perform_install(
                source_dir=source_dir,
                install_mode="global",
                requested_locale=None,
                bootstrap_runner=lambda _: None,
            )

        self.assertIn("First global install requires --locale", str(exc.exception))

    def test_perform_global_install_creates_managed_copy_and_reuses_manifest_locale(self) -> None:
        source_dir = self.make_source_skill_dir()

        first_result = ss.perform_install(
            source_dir=source_dir,
            install_mode="global",
            requested_locale="ru",
            bootstrap_runner=lambda _: None,
        )
        second_result = ss.perform_install(
            source_dir=source_dir,
            install_mode="global",
            requested_locale=None,
            bootstrap_runner=lambda _: None,
        )

        self.assertEqual(
            first_result.runtime_dir.resolve(),
            (self.xdg_data_home / "agents" / "skills" / "skill-youtrack").resolve(),
        )
        self.assertEqual(second_result.locale_mode, "ru")
        self.assertTrue((first_result.runtime_dir / ss.MANIFEST_FILENAME).exists())
        self.assertEqual(
            (self.home / ".claude" / "skills" / "skill-youtrack").resolve(),
            first_result.runtime_dir.resolve(),
        )
        self.assertEqual(
            (self.home / ".codex" / "skills" / "skill-youtrack").resolve(),
            first_result.runtime_dir.resolve(),
        )
        self.assertIn(
            'description: "Русское описание"',
            (first_result.runtime_dir / "SKILL.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            'description: English source description',
            (source_dir / "SKILL.md").read_text(encoding="utf-8"),
        )

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
                bootstrap_runner=lambda _: None,
            )

            with self.assertRaises(ss.SetupError) as exc:
                ss.perform_install(
                    source_dir=source_dir,
                    install_mode="local",
                    requested_locale="en",
                    repo_root=repo_root,
                    bootstrap_runner=lambda _: None,
                )

        self.assertEqual(
            first_result.runtime_dir.resolve(),
            (repo_root / ".skills" / "skill-youtrack").resolve(),
        )
        self.assertEqual(
            os.readlink(repo_root / ".claude" / "skills" / "skill-youtrack"),
            "../../.skills/skill-youtrack",
        )
        self.assertEqual(
            os.readlink(repo_root / ".codex" / "skills" / "skill-youtrack"),
            "../../.skills/skill-youtrack",
        )
        self.assertIn("project-fixed", str(exc.exception))
        rendered_skill = (first_result.runtime_dir / "SKILL.md").read_text(encoding="utf-8")
        rendered_yaml = (first_result.runtime_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('description: "[локально] Русское описание"', rendered_skill)
        self.assertIn('display_name: "[локально] Русский Display"', rendered_yaml)
        self.assertIn('short_description: "[локально] Русский Short"', rendered_yaml)
        self.assertIn('default_prompt: "Используй $skill-youtrack по-русски."', rendered_yaml)

    def test_resolve_source_dir_prefers_manifest_source_dir(self) -> None:
        source_dir = self.make_source_skill_dir().resolve()
        installed_dir = self.root / "installed" / "skill-youtrack"
        installed_dir.mkdir(parents=True, exist_ok=True)
        ss.write_install_manifest(
            skill_dir=installed_dir,
            skill_name="skill-youtrack",
            install_mode="global",
            locale_mode="en",
            source_dir=source_dir,
            runtime_dir=installed_dir,
        )

        self.assertEqual(ss.resolve_source_dir(installed_dir), source_dir)


if __name__ == "__main__":
    unittest.main()

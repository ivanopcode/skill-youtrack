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

import instance_runtime as ir


class InstanceRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.xdg_config_home = self.root / "xdg-config"
        self.env_patcher = mock.patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(self.xdg_config_home)},
            clear=False,
        )
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

    def make_global_skill_dir(self) -> Path:
        skill_dir = self.root / "agents" / "skills" / "youtrack-cli"
        skill_dir.mkdir(parents=True, exist_ok=True)
        return skill_dir

    def make_local_skill_dir(self) -> Path:
        skill_dir = self.root / "repo" / ".skills" / "youtrack-cli"
        skill_dir.mkdir(parents=True, exist_ok=True)
        return skill_dir

    def test_detect_install_context_global_and_local(self) -> None:
        global_context = ir.detect_install_context(self.make_global_skill_dir())
        self.assertEqual(global_context.install_kind, "global")
        self.assertEqual(global_context.install_root, self.make_global_skill_dir().resolve())
        self.assertTrue(str(global_context.state_path).startswith(str(self.xdg_config_home)))

        local_context = ir.detect_install_context(self.make_local_skill_dir())
        self.assertEqual(local_context.install_kind, "local")
        self.assertEqual(local_context.install_root, (self.root / "repo").resolve())
        self.assertTrue(str(local_context.state_path).startswith(str(self.xdg_config_home)))
        self.assertFalse(str(local_context.state_path).startswith(str((self.root / "repo").resolve())))

    def test_registry_bootstraps_from_instance_configs(self) -> None:
        instances_dir = self.xdg_config_home / "youtrack-cli" / "instances"
        instances_dir.mkdir(parents=True, exist_ok=True)
        (instances_dir / "primary.env").write_text("YOUTRACK_BASE_URL=https://prod\n", encoding="utf-8")
        (instances_dir / "staging.env").write_text("YOUTRACK_BASE_URL=https://stage\n", encoding="utf-8")

        self.assertEqual(ir.registered_instances(), ["primary", "staging"])
        payload = json.loads((self.xdg_config_home / "youtrack-cli" / "registry.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["instances"], ["primary", "staging"])

    def test_resolve_instance_selection_precedence(self) -> None:
        skill_dir = self.make_global_skill_dir()
        ir.save_registry(["flag-one", "env-one", "active-one"])
        ir.set_active_instance(ir.detect_install_context(skill_dir), "active-one")

        selection = ir.resolve_instance_selection(skill_dir, "flag-one")
        self.assertEqual((selection.label, selection.source), ("flag-one", "flag"))

        with mock.patch.dict(os.environ, {"YOUTRACK_INSTANCE": "env-one"}, clear=False):
            selection = ir.resolve_instance_selection(skill_dir)
            self.assertEqual((selection.label, selection.source), ("env-one", "env"))

        selection = ir.resolve_instance_selection(skill_dir)
        self.assertEqual((selection.label, selection.source), ("active-one", "active"))

        ir.save_registry(["sole-one"])
        ir.set_active_instance(ir.detect_install_context(skill_dir), None)
        selection = ir.resolve_instance_selection(skill_dir)
        self.assertEqual((selection.label, selection.source), ("sole-one", "sole"))

    def test_use_instance_pins_current_install(self) -> None:
        skill_dir = self.make_global_skill_dir()
        ir.save_registry(["primary"])

        with mock.patch.object(ir, "instance_is_ready", return_value=True):
            payload = ir.use_instance(skill_dir, "primary")

        self.assertEqual(payload["instance"]["label"], "primary")
        self.assertEqual(ir.get_active_instance(ir.detect_install_context(skill_dir)), "primary")

    def test_scoped_board_ids_roundtrip_and_instance_record(self) -> None:
        skill_dir = self.make_global_skill_dir()
        config_path = ir.config_path_for_label("primary")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("YOUTRACK_BASE_URL='https://prod'\n", encoding="utf-8")

        board_ids = ir.set_instance_scoped_board_ids("primary", ["agiles/83-2561", "195-1", "83-2561"])
        self.assertEqual(board_ids, ["83-2561", "195-1"])
        self.assertEqual(ir.scoped_board_ids_for_label("primary"), ["83-2561", "195-1"])

        record = ir.instance_record(skill_dir, "primary", "primary")
        self.assertEqual(record["scoped_board_ids"], ["83-2561", "195-1"])
        self.assertEqual(record["base_url"], "https://prod")

        ir.clear_instance_scoped_board_ids("primary")
        self.assertEqual(ir.scoped_board_ids_for_label("primary"), [])

    def test_rename_instance_updates_registry_configs_and_all_install_states(self) -> None:
        skill_dir = self.make_global_skill_dir()
        ir.save_registry(["primary"])
        source_config = ir.config_path_for_label("primary")
        source_config.parent.mkdir(parents=True, exist_ok=True)
        source_config.write_text("YOUTRACK_BASE_URL=https://prod\n", encoding="utf-8")

        current_context = ir.detect_install_context(skill_dir)
        ir.set_active_instance(current_context, "primary")
        other_state = self.xdg_config_home / "youtrack-cli" / "installs" / "other.json"
        other_state.parent.mkdir(parents=True, exist_ok=True)
        other_state.write_text(
            json.dumps(
                {
                    "install_id": "other",
                    "install_root": "/tmp/other",
                    "install_kind": "local",
                    "active_instance": "primary",
                    "updated_at": None,
                }
            ),
            encoding="utf-8",
        )

        with mock.patch.object(ir, "_collect_decrypted_credentials", return_value={"youtrack_token": "secret"}), \
             mock.patch.object(ir, "_store_decrypted_credentials") as store_credentials, \
             mock.patch.object(ir, "_delete_raw_keychain_entry", return_value=True):
            payload = ir.rename_instance(skill_dir, "primary", "main")

        self.assertEqual(payload["renamed"], {"from": "primary", "to": "main"})
        self.assertFalse(source_config.exists())
        self.assertTrue(ir.config_path_for_label("main").exists())
        self.assertEqual(ir.registered_instances(), ["main"])
        self.assertEqual(ir.get_active_instance(current_context), "main")
        other_payload = json.loads(other_state.read_text(encoding="utf-8"))
        self.assertEqual(other_payload["active_instance"], "main")
        store_credentials.assert_called_once_with("main", {"youtrack_token": "secret"})

    def test_delete_instance_artifacts_clears_registry_and_active_references(self) -> None:
        skill_dir = self.make_global_skill_dir()
        ir.save_registry(["primary"])
        config_path = ir.config_path_for_label("primary")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("YOUTRACK_BASE_URL=https://prod\n", encoding="utf-8")

        current_context = ir.detect_install_context(skill_dir)
        ir.set_active_instance(current_context, "primary")
        other_state = self.xdg_config_home / "youtrack-cli" / "installs" / "other.json"
        other_state.parent.mkdir(parents=True, exist_ok=True)
        other_state.write_text(
            json.dumps(
                {
                    "install_id": "other",
                    "install_root": "/tmp/other",
                    "install_kind": "global",
                    "active_instance": "primary",
                    "updated_at": None,
                }
            ),
            encoding="utf-8",
        )

        with mock.patch.object(ir, "_delete_raw_keychain_entry", return_value=True):
            payload = ir.delete_instance_artifacts("primary")

        self.assertEqual(payload["label"], "primary")
        self.assertEqual(ir.registered_instances(), [])
        self.assertFalse(config_path.exists())
        self.assertIsNone(ir.get_active_instance(current_context))
        self.assertIsNone(json.loads(other_state.read_text(encoding="utf-8"))["active_instance"])


if __name__ == "__main__":
    unittest.main()

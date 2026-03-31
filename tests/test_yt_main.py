from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest import mock

import sys

import click


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import yt_main
from instance_runtime import CredentialManager, InstanceSelection


class YtMainTest(unittest.TestCase):
    def test_login_requires_explicit_instance(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                yt_main.main(["auth", "login", "--base-url", "https://example", "--token", "secret"])

        self.assertEqual(exc.exception.code, 1)
        self.assertIn("auth login requires --instance <label>", stderr.getvalue())

    def test_run_upstream_injects_config_and_keychain_service(self) -> None:
        captured: dict[str, object] = {}

        def fake_main(*, args: list[str], prog_name: str, standalone_mode: bool) -> None:
            captured["args"] = list(args)
            captured["prog_name"] = prog_name
            captured["standalone_mode"] = standalone_mode
            captured["service"] = CredentialManager.KEYRING_SERVICE

        with mock.patch.object(yt_main.upstream_main, "main", side_effect=fake_main):
            yt_main.run_upstream(["boards", "list"], selection_label="primary")

        self.assertEqual(captured["prog_name"], "yt")
        self.assertEqual(captured["standalone_mode"], False)
        self.assertEqual(captured["service"], "youtrack-cli:primary")
        self.assertEqual(captured["args"][:2], ["--config", str(yt_main.config_path_for_label("primary"))])

    def test_run_upstream_formats_click_exceptions_without_traceback(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with mock.patch.object(
                yt_main.upstream_main,
                "main",
                side_effect=click.ClickException("boom"),
            ):
                with self.assertRaises(SystemExit) as exc:
                    yt_main.run_upstream(["boards", "list"], selection_label="primary")

        self.assertEqual(exc.exception.code, 1)
        self.assertIn("boom", stderr.getvalue())

    def test_handle_auth_login_auto_pins_by_default(self) -> None:
        args = yt_main.WrapperArgs(
            instance="primary",
            board_ids=[],
            no_auto_pin=False,
            forwarded=["auth", "login", "--base-url", "https://example", "--token", "secret"],
        )

        with mock.patch.object(
            yt_main,
            "resolve_login_instance",
            return_value=InstanceSelection("primary", "flag", Path("/tmp/primary.env")),
        ), mock.patch.object(yt_main, "run_upstream") as run_upstream, \
            mock.patch.object(yt_main, "register_instance") as register_instance, \
            mock.patch.object(yt_main, "detect_install_context", return_value=mock.sentinel.context), \
            mock.patch.object(yt_main, "set_active_instance") as set_active:
            yt_main.handle_auth_login(Path("/skill"), args)

        run_upstream.assert_called_once()
        register_instance.assert_called_once_with("primary")
        set_active.assert_called_once_with(mock.sentinel.context, "primary")

    def test_handle_auth_login_respects_no_auto_pin(self) -> None:
        args = yt_main.WrapperArgs(
            instance="primary",
            board_ids=[],
            no_auto_pin=True,
            forwarded=["auth", "login", "--base-url", "https://example", "--token", "secret"],
        )

        with mock.patch.object(
            yt_main,
            "resolve_login_instance",
            return_value=InstanceSelection("primary", "flag", Path("/tmp/primary.env")),
        ), mock.patch.object(yt_main, "run_upstream"), \
            mock.patch.object(yt_main, "register_instance"), \
            mock.patch.object(yt_main, "set_active_instance") as set_active:
            yt_main.handle_auth_login(Path("/skill"), args)

        set_active.assert_not_called()

    def test_handle_auth_login_stores_scoped_board_ids(self) -> None:
        args = yt_main.WrapperArgs(
            instance="primary",
            board_ids=["83-2561", "agiles/195-1"],
            no_auto_pin=False,
            forwarded=["auth", "login", "--base-url", "https://example", "--token", "secret"],
        )

        with mock.patch.object(
            yt_main,
            "resolve_login_instance",
            return_value=InstanceSelection("primary", "flag", Path("/tmp/primary.env")),
        ), mock.patch.object(yt_main, "run_upstream"), \
            mock.patch.object(yt_main, "register_instance"), \
            mock.patch.object(yt_main, "set_instance_scoped_board_ids") as set_scope, \
            mock.patch.object(yt_main, "detect_install_context", return_value=mock.sentinel.context), \
            mock.patch.object(yt_main, "set_active_instance"):
            yt_main.handle_auth_login(Path("/skill"), args)

        set_scope.assert_called_once_with("primary", ["83-2561", "agiles/195-1"])

    def test_handle_auth_logout_cleans_up_when_instance_not_ready_afterwards(self) -> None:
        args = yt_main.WrapperArgs(instance="primary", board_ids=[], no_auto_pin=False, forwarded=["auth", "logout"])

        with mock.patch.object(
            yt_main,
            "resolve_instance_selection",
            return_value=InstanceSelection("primary", "flag", Path("/tmp/primary.env")),
        ), mock.patch.object(yt_main, "run_upstream"), \
            mock.patch.object(yt_main, "instance_is_ready", return_value=False), \
            mock.patch.object(yt_main, "delete_instance_artifacts") as delete_artifacts:
            yt_main.handle_auth_logout(Path("/skill"), args)

        delete_artifacts.assert_called_once_with("primary")

    def test_instances_subcommand_rejects_no_auto_pin(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                yt_main.main(["--no-auto-pin", "instances", "list"])

        self.assertEqual(exc.exception.code, 1)
        self.assertIn("--no-auto-pin is only valid", stderr.getvalue())

    def test_parse_wrapper_args_collects_board_ids(self) -> None:
        parsed = yt_main.parse_wrapper_args(
            [
                "--instance",
                "primary",
                "--board-id",
                "83-2561",
                "--board-id=agiles/195-1",
                "auth",
                "login",
            ]
        )
        self.assertEqual(parsed.instance, "primary")
        self.assertEqual(parsed.board_ids, ["83-2561", "agiles/195-1"])
        self.assertEqual(parsed.forwarded, ["auth", "login"])

    def test_instances_scope_set_updates_selected_instance(self) -> None:
        args = yt_main.WrapperArgs(
            instance=None,
            board_ids=[],
            no_auto_pin=False,
            forwarded=["instances", "scope", "set", "primary", "83-2561", "agiles/195-1"],
        )

        with mock.patch.object(yt_main, "instance_known", return_value=True), \
            mock.patch.object(yt_main, "set_instance_scoped_board_ids") as set_scope, \
            mock.patch.object(
                yt_main,
                "instances_current_payload",
                return_value={"instance": {"label": "primary", "scoped_board_ids": ["83-2561", "195-1"]}},
            ), \
            mock.patch.object(yt_main, "print_json") as print_json:
            yt_main.handle_instances_command(Path("/skill"), args)

        set_scope.assert_called_once_with("primary", ["83-2561", "agiles/195-1"])
        print_json.assert_called_once_with(
            {"instance": {"label": "primary", "scoped_board_ids": ["83-2561", "195-1"]}}
        )


if __name__ == "__main__":
    unittest.main()

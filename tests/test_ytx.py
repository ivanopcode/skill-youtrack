from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock

import sys


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ytx


class YtxTest(unittest.TestCase):
    def test_parser_accepts_top_level_instance_and_instances_command(self) -> None:
        parser = ytx.build_parser()
        args = parser.parse_args(["--instance", "primary", "instances", "current"])
        self.assertEqual(args.instance, "primary")
        self.assertEqual(args.command, "instances")
        self.assertEqual(args.instances_command, "current")

        args = parser.parse_args(["--instance", "primary", "board", "list"])
        self.assertEqual(args.instance, "primary")
        self.assertEqual(args.command, "board")
        self.assertEqual(args.board_command, "list")

    def test_main_async_routes_board_commands_through_activated_auth_manager(self) -> None:
        context_manager = mock.MagicMock()
        context_manager.__enter__.return_value = (mock.sentinel.context, mock.sentinel.selection, mock.sentinel.auth)
        context_manager.__exit__.return_value = False

        with mock.patch.object(sys, "argv", ["ytx.py", "--instance", "primary", "board", "list"]), \
            mock.patch.object(ytx, "activated_auth_manager", return_value=context_manager) as activated_auth_manager, \
            mock.patch.object(ytx, "handle_board", new=AsyncMock()) as handle_board:
            asyncio.run(ytx.main_async())

        activated_auth_manager.assert_called_once()
        handle_board.assert_awaited_once()

    def test_main_async_handles_instances_use(self) -> None:
        with mock.patch.object(sys, "argv", ["ytx.py", "instances", "use", "primary"]), \
            mock.patch.object(ytx, "use_instance", return_value={"instance": {"label": "primary"}}) as use_instance, \
            mock.patch.object(ytx, "dump") as dump:
            asyncio.run(ytx.main_async())

        use_instance.assert_called_once_with(Path(ytx.__file__).resolve().parent.parent, "primary")
        dump.assert_called_once_with({"instance": {"label": "primary"}})


if __name__ == "__main__":
    unittest.main()

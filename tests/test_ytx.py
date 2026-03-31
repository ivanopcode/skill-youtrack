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

        args = parser.parse_args(["--instance", "primary", "board", "list", "--scoped"])
        self.assertTrue(args.scoped)

        args = parser.parse_args(["--instance", "primary", "board", "scoped-issues", "--mine"])
        self.assertEqual(args.board_command, "scoped-issues")
        self.assertTrue(args.mine)

    def test_main_async_routes_board_commands_through_activated_auth_manager(self) -> None:
        context_manager = mock.MagicMock()
        selection = mock.Mock(label="primary")
        context_manager.__enter__.return_value = (mock.sentinel.context, selection, mock.sentinel.auth)
        context_manager.__exit__.return_value = False

        with mock.patch.object(sys, "argv", ["ytx.py", "--instance", "primary", "board", "list"]), \
            mock.patch.object(ytx, "activated_auth_manager", return_value=context_manager) as activated_auth_manager, \
            mock.patch.object(ytx, "handle_board", new=AsyncMock()) as handle_board:
            asyncio.run(ytx.main_async())

        activated_auth_manager.assert_called_once()
        handle_board.assert_awaited_once()

    def test_handle_board_scoped_issues_uses_configured_scoped_boards(self) -> None:
        args = mock.Mock(
            board_command="scoped-issues",
            mine=True,
            assignee=None,
            source="web",
            me_from=None,
            state=None,
            limit=None,
            raw=False,
        )

        with mock.patch.object(ytx, "scoped_board_ids_for_label", return_value=["83-2561", "195-1"]), \
            mock.patch.object(
                ytx,
                "build_board_issues_payload",
                new=AsyncMock(side_effect=[{"board_id": "83-2561"}, {"board_id": "195-1"}]),
            ) as build_payload, \
            mock.patch.object(ytx, "dump") as dump:
            asyncio.run(ytx.handle_board(args, mock.sentinel.auth_manager, "primary"))

        self.assertEqual(build_payload.await_count, 2)
        dump.assert_called_once_with(
            {
                "scoped_board_ids": ["83-2561", "195-1"],
                "source": "web",
                "board_count": 2,
                "boards": [{"board_id": "83-2561"}, {"board_id": "195-1"}],
            }
        )

    def test_main_async_handles_instances_use(self) -> None:
        with mock.patch.object(sys, "argv", ["ytx.py", "instances", "use", "primary"]), \
            mock.patch.object(ytx, "use_instance", return_value={"instance": {"label": "primary"}}) as use_instance, \
            mock.patch.object(ytx, "dump") as dump:
            asyncio.run(ytx.main_async())

        use_instance.assert_called_once_with(Path(ytx.__file__).resolve().parent.parent, "primary")
        dump.assert_called_once_with({"instance": {"label": "primary"}})


if __name__ == "__main__":
    unittest.main()

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
    def sample_issue(self) -> dict[str, object]:
        return {
            "id": "92-1",
            "idReadable": "PMA-21079",
            "summary": "Example issue",
            "description": "Example description",
            "project": {"name": "Partners Mobile App"},
            "assignee": {"login": "oparin.ivan3", "fullName": "Иван Опарин"},
            "customFields": [
                {"name": "State", "value": {"name": "In Progress"}},
                {"name": "Type", "value": {"name": "Epic"}},
                {"name": "Priority", "value": {"name": "Major"}},
            ],
        }

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

    def test_normalize_issue_includes_url_when_base_url_is_available(self) -> None:
        normalized = ytx.normalize_issue(
            self.sample_issue(),
            base_url="https://youtrack.example.com/",
        )

        self.assertEqual(normalized["id"], "PMA-21079")
        self.assertEqual(
            normalized["url"],
            "https://youtrack.example.com/issue/PMA-21079",
        )

    def test_normalize_issue_omits_url_when_base_url_is_missing(self) -> None:
        normalized = ytx.normalize_issue(self.sample_issue())

        self.assertNotIn("url", normalized)

    def test_build_board_issues_payload_includes_issue_urls(self) -> None:
        service = mock.Mock()
        service.get_board = AsyncMock(
            return_value={
                "status": "success",
                "data": {
                    "id": "83-2561",
                    "name": "PMA iOS Core",
                    "currentSprint": {"id": "84-96818", "name": "Спринт 47"},
                },
            }
        )
        service.get_sprint = AsyncMock(
            return_value={
                "status": "success",
                "data": {
                    "id": "84-96818",
                    "name": "Спринт 47",
                    "issues": [self.sample_issue()],
                    "unresolvedIssuesCount": 1,
                },
            }
        )

        with mock.patch.object(
            ytx,
            "resolve_assignee_filter",
            new=AsyncMock(return_value=(None, None)),
        ):
            payload = asyncio.run(
                ytx.build_board_issues_payload(
                    service,
                    mock.sentinel.auth_manager,
                    board_id="83-2561",
                    sprint_id=None,
                    source="web",
                    assignee=None,
                    me_from=None,
                    state=None,
                    limit=None,
                    raw=False,
                    base_url="https://youtrack.example.com/",
                )
            )

        self.assertEqual(payload["issue_count"], 1)
        self.assertEqual(
            payload["issues"][0]["url"],
            "https://youtrack.example.com/issue/PMA-21079",
        )

    def test_handle_issue_show_includes_url(self) -> None:
        args = mock.Mock(issue_command="show", issue_id="PMA-21079", raw=False)

        with mock.patch.object(
            ytx,
            "get_issue_data",
            new=AsyncMock(return_value=self.sample_issue()),
        ), mock.patch.object(ytx, "dump") as dump:
            asyncio.run(
                ytx.handle_issue(
                    args,
                    mock.sentinel.auth_manager,
                    base_url="https://youtrack.example.com",
                )
            )

        payload = dump.call_args.args[0]
        self.assertEqual(payload["url"], "https://youtrack.example.com/issue/PMA-21079")

    def test_handle_issue_show_raw_does_not_inject_url(self) -> None:
        args = mock.Mock(issue_command="show", issue_id="PMA-21079", raw=True)

        with mock.patch.object(
            ytx,
            "get_issue_data",
            new=AsyncMock(return_value=self.sample_issue()),
        ), mock.patch.object(ytx, "dump") as dump:
            asyncio.run(
                ytx.handle_issue(
                    args,
                    mock.sentinel.auth_manager,
                    base_url="https://youtrack.example.com",
                )
            )

        payload = dump.call_args.args[0]
        self.assertNotIn("url", payload)

    def test_handle_issue_search_includes_urls(self) -> None:
        args = mock.Mock(issue_command="search", query="Assignee: me", project_id=None)
        manager = mock.Mock()
        manager.search_issues = AsyncMock(
            return_value={"status": "success", "data": [self.sample_issue()]}
        )

        with mock.patch.object(ytx, "IssueManager", return_value=manager), \
            mock.patch.object(ytx, "dump") as dump:
            asyncio.run(
                ytx.handle_issue(
                    args,
                    mock.sentinel.auth_manager,
                    base_url="https://youtrack.example.com",
                )
            )

        payload = dump.call_args.args[0]
        self.assertEqual(payload[0]["url"], "https://youtrack.example.com/issue/PMA-21079")


if __name__ == "__main__":
    unittest.main()

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
            "resolved": None,
            "project": {"name": "Partners Mobile App"},
            "assignee": {"login": "oparin.ivan3", "fullName": "Иван Опарин"},
            "customFields": [
                {"name": "State", "value": {"name": "In Progress"}},
                {"name": "Type", "value": {"name": "Epic"}},
                {"name": "Priority", "value": {"name": "Major"}},
                {"name": "Initiator", "value": {"name": "Малышев Максим"}},
            ],
        }

    def sample_board(self) -> dict[str, object]:
        return {
            "id": "83-2561",
            "name": "PMA iOS Core",
            "projects": [{"id": "77-344", "name": "Partners Mobile App", "shortName": "PMA"}],
            "currentSprint": {"id": "84-96818", "name": "Спринт 47"},
            "sprints": [],
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

        args = parser.parse_args(["board", "current", "--board", "83-2561"])
        self.assertEqual(args.board_command, "current")
        self.assertEqual(args.board, "83-2561")

        args = parser.parse_args(["board", "tasks", "--board", "83-2561", "--assignee", "me"])
        self.assertEqual(args.board_command, "tasks")
        self.assertEqual(args.assignee, "me")

        args = parser.parse_args(["board", "create-subtask", "--parent", "PMA-1", "--summary", "Test"])
        self.assertEqual(args.board_command, "create-subtask")
        self.assertEqual(args.parent_issue_id, "PMA-1")

        args = parser.parse_args(["issue", "create", "--project", "PMA", "--summary", "Test"])
        self.assertEqual(args.issue_command, "create")
        self.assertEqual(args.project, "PMA")

        args = parser.parse_args(["issue", "link", "--source", "PMA-1", "--target", "PMA-2", "--type", "Subtask"])
        self.assertEqual(args.issue_command, "link")
        self.assertEqual(args.link_type, "Subtask")

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

    def test_resolve_target_board_uses_single_scoped_board_when_board_omitted(self) -> None:
        service = mock.Mock()
        service.get_board = AsyncMock(return_value={"status": "success", "data": self.sample_board()})

        with mock.patch.object(ytx, "scoped_board_ids_for_label", return_value=["83-2561"]):
            board, scoped_ids = asyncio.run(ytx.resolve_target_board(service, "wb", None))

        self.assertEqual(board["id"], "83-2561")
        self.assertEqual(scoped_ids, ["83-2561"])

    def test_resolve_target_board_requires_board_when_scope_has_multiple_boards(self) -> None:
        service = mock.Mock()
        service.get_board = AsyncMock(
            side_effect=[
                {"status": "success", "data": self.sample_board()},
                {"status": "success", "data": {**self.sample_board(), "id": "83-9999", "name": "Other"}},
            ]
        )

        with mock.patch.object(ytx, "scoped_board_ids_for_label", return_value=["83-2561", "83-9999"]):
            with self.assertRaises(SystemExit):
                asyncio.run(ytx.resolve_target_board(service, "wb", None))

    def test_build_typed_field_payload_supports_multi_enum_and_period(self) -> None:
        multi_enum = ytx.build_typed_field_payload(
            {
                "field_name": "Stream",
                "issue_field_type": "MultiEnumIssueCustomField",
                "bundle_element_type": "EnumBundleElement",
            },
            ["Core", "RKI"],
        )
        self.assertEqual(multi_enum["$type"], "MultiEnumIssueCustomField")
        self.assertEqual([item["name"] for item in multi_enum["value"]], ["Core", "RKI"])

        period = ytx.build_typed_field_payload(
            {"field_name": "Pre-assessment", "issue_field_type": "PeriodIssueCustomField"},
            ["1d"],
        )
        self.assertEqual(period["$type"], "PeriodIssueCustomField")
        self.assertEqual(period["value"]["presentation"], "1d")

        single_user = ytx.build_typed_field_payload(
            {"field_name": "Assignee", "project_field_type": "UserProjectCustomField"},
            ["oparin.ivan3"],
        )
        self.assertEqual(single_user["$type"], "SingleUserIssueCustomField")
        self.assertEqual(single_user["value"]["login"], "oparin.ivan3")

    def test_build_board_tasks_payload_filters_initiator_and_active_only(self) -> None:
        board = self.sample_board()
        matching_issue = self.sample_issue()
        done_issue = {**self.sample_issue(), "idReadable": "PMA-999", "resolved": 123}
        other_initiator = {
            **self.sample_issue(),
            "idReadable": "PMA-998",
            "customFields": [
                {"name": "State", "value": {"name": "In Progress"}},
                {"name": "Type", "value": {"name": "Task"}},
                {"name": "Priority", "value": {"name": "Normal"}},
                {"name": "Initiator", "value": {"name": "Другой Человек"}},
            ],
        }

        with mock.patch.object(
            ytx,
            "resolve_target_board",
            new=AsyncMock(return_value=(board, ["83-2561"])),
        ), mock.patch.object(
            ytx,
            "build_board_issues_payload",
            new=AsyncMock(
                return_value={
                    "board_id": "83-2561",
                    "board_name": "PMA iOS Core",
                    "sprint_id": "84-96818",
                    "sprint_name": "Спринт 47",
                    "filters": {"assignee": None},
                    "issues": [matching_issue, done_issue, other_initiator],
                }
            ),
        ):
            payload = asyncio.run(
                ytx.build_board_tasks_payload(
                    mock.sentinel.service,
                    mock.sentinel.auth_manager,
                    selection_label="wb",
                    board_ref=None,
                    source="web",
                    assignee=None,
                    me_from=None,
                    initiator="Малышев Максим",
                    state=None,
                    active_only=True,
                    limit=None,
                    base_url="https://youtrack.example.com",
                )
            )

        self.assertEqual(payload["issue_count"], 1)
        self.assertEqual(payload["issues"][0]["id"], "PMA-21079")
        self.assertEqual(payload["board_url"], "https://youtrack.example.com/agiles/83-2561/current")

    def test_prepare_issue_create_operation_builds_preview(self) -> None:
        board = self.sample_board()
        project = {"id": "77-344", "shortName": "PMA", "name": "Partners Mobile App"}
        field_payloads = [
            {
                "$type": "MultiEnumIssueCustomField",
                "name": "Stream",
                "value": [{"$type": "EnumBundleElement", "name": "Core"}],
            }
        ]
        field_previews = [{"name": "Stream", "type": "MultiEnumIssueCustomField", "value": ["Core"], "required": True}]

        with mock.patch.object(
            ytx,
            "resolve_target_board",
            new=AsyncMock(return_value=(board, ["83-2561"])),
        ), mock.patch.object(
            ytx,
            "resolve_project_context",
            new=AsyncMock(return_value=project),
        ), mock.patch.object(
            ytx,
            "resolve_user_reference",
            new=AsyncMock(return_value=("oparin.ivan3", {"user": {"login": "oparin.ivan3"}})),
        ), mock.patch.object(
            ytx,
            "build_project_field_payloads",
            new=AsyncMock(return_value=(field_payloads, field_previews)),
        ):
            prepared = asyncio.run(
                ytx.prepare_issue_create_operation(
                    mock.sentinel.auth_manager,
                    selection_label="wb",
                    summary="Worktree setup",
                    description="Desc",
                    project_ref=None,
                    board_ref=None,
                    use_current_sprint=True,
                    parent_issue_id="PMA-21079",
                    type_name="Task",
                    priority="Normal",
                    assignee=None,
                    mine=True,
                    me_from="git-email-localpart",
                    raw_fields=[],
                )
            )

        self.assertEqual(prepared["issue_payload"]["project"]["id"], "77-344")
        self.assertEqual(prepared["preview"]["operation"], "create-subtask")
        self.assertEqual(len(prepared["preview"]["planned_actions"]), 3)

    def test_apply_issue_create_operation_returns_partial_success_when_link_fails(self) -> None:
        prepared = {
            "issue_payload": {"project": {"id": "77-344"}, "summary": "Worktree setup"},
            "parent_issue_id": "PMA-21079",
            "board": self.sample_board(),
            "sprint_name": "Спринт 47",
        }

        workflow_service = mock.Mock()
        workflow_service.create_issue = AsyncMock(return_value={"status": "success", "data": {"id": "92-1"}})
        issue_service = mock.Mock()
        issue_service.create_link = AsyncMock(return_value={"status": "error", "message": "boom"})

        with mock.patch.object(ytx, "WorkflowIssueService", return_value=workflow_service), \
            mock.patch.object(ytx, "IssueService", return_value=issue_service), \
            mock.patch.object(ytx, "IssueManager"), \
            mock.patch.object(
                ytx,
                "build_issue_brief_payload",
                new=AsyncMock(
                    return_value={
                        "id": "PMA-22199",
                        "summary": "Worktree setup",
                        "url": "https://youtrack.example.com/issue/PMA-22199",
                    }
                ),
            ):
            result = asyncio.run(
                ytx.apply_issue_create_operation(
                    mock.sentinel.auth_manager,
                    prepared,
                    base_url="https://youtrack.example.com",
                )
            )

        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["created_issue"]["id"], "PMA-22199")

    def test_preview_or_apply_issue_link_preview_mode(self) -> None:
        payload = asyncio.run(
            ytx.preview_or_apply_issue_link(
                mock.sentinel.auth_manager,
                source_issue_id="PMA-1",
                target_issue_id="PMA-2",
                link_type="Subtask",
                apply=False,
            )
        )

        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["planned_actions"][0]["type"], "link_issue")

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

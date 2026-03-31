#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Optional

logging.disable(logging.CRITICAL)

from youtrack_cli.auth import AuthManager
from youtrack_cli.custom_field_manager import CustomFieldManager
from youtrack_cli.managers.issues import IssueManager
from youtrack_cli.services.base import BaseService

from instance_runtime import (
    InstanceRuntimeError,
    activated_auth_manager,
    base_url_for_label,
    instances_current_payload,
    instances_list_payload,
    scoped_board_ids_for_label,
    use_instance,
)


class AgileService(BaseService):
    ISSUE_FIELDS = (
        "id,idReadable,summary,description,created,updated,"
        "project(id,name),"
        "assignee(id,login,name,fullName),"
        "customFields(name,value(id,login,name,fullName,text,presentation))"
    )

    async def list_boards(self, project_id: Optional[str] = None) -> dict[str, Any]:
        params = {
            "$top": 2000,
            "fields": (
                "id,name,"
                "projects(id,name),"
                "owner(id,login,name,fullName),"
                "currentSprint(id,name,start,finish)"
            ),
        }
        if project_id:
            params["project"] = project_id
        response = await self._make_request("GET", "agiles", params=params)
        return await self._handle_response(response)

    async def get_board(self, board_id: str) -> dict[str, Any]:
        params = {
            "fields": (
                "id,name,"
                "projects(id,name),"
                "owner(id,login,name,fullName),"
                "currentSprint(id,name,start,finish),"
                "sprints(id,name,start,finish,isDefault),"
                "columnSettings(columns(id,presentation)),"
                "sprintsSettings(disableSprints)"
            )
        }
        response = await self._make_request("GET", f"agiles/{board_id}", params=params)
        return await self._handle_response(response)

    async def list_sprints(self, board_id: str) -> dict[str, Any]:
        params = {"fields": "id,name,start,finish,isDefault"}
        response = await self._make_request("GET", f"agiles/{board_id}/sprints", params=params)
        return await self._handle_response(response)

    async def get_sprint(
        self,
        board_id: str,
        sprint_id: str,
        include_issues: bool = False,
    ) -> dict[str, Any]:
        fields = ["id,name,start,finish,isDefault,unresolvedIssuesCount"]
        if include_issues:
            fields.append(f"issues({self.ISSUE_FIELDS})")
        params = {"fields": ",".join(fields)}
        response = await self._make_request("GET", f"agiles/{board_id}/sprints/{sprint_id}", params=params)
        return await self._handle_response(response)

    async def get_sprint_issues(self, board_id: str, sprint_id: str) -> dict[str, Any]:
        params = {"fields": self.ISSUE_FIELDS}
        response = await self._make_request("GET", f"agiles/{board_id}/sprints/{sprint_id}/issues", params=params)
        return await self._handle_response(response)


class CommandService(BaseService):
    COMMAND_FIELDS = (
        "$type,caret,commands($type,description,error,id),comment,id,"
        "issues($type,id,idReadable,numberInProject),query,"
        "suggestions($type,caret,completionEnd,completionStart,description,id,matchingEnd,matchingStart,option,prefix,suffix)"
    )

    async def assist(
        self,
        issue_ids: list[str],
        query: str,
        caret: Optional[int] = None,
    ) -> dict[str, Any]:
        payload = {
            "query": query,
            "caret": len(query) if caret is None else caret,
            "issues": [{"id": issue_id} for issue_id in issue_ids],
        }
        response = await self._make_request(
            "POST",
            "commands/assist",
            params={"fields": self.COMMAND_FIELDS},
            json_data=payload,
        )
        return await self._handle_response(response)

    async def apply(
        self,
        issue_ids: list[str],
        query: str,
        comment: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = {
            "query": query,
            "silent": True,
            "issues": [{"id": issue_id} for issue_id in issue_ids],
        }
        if comment:
            payload["comment"] = comment
        response = await self._make_request(
            "POST",
            "commands",
            params={"fields": self.COMMAND_FIELDS},
            json_data=payload,
        )
        return await self._handle_response(response, success_codes=[200, 201])


class UserService(BaseService):
    async def find_users(self, query: str, top: int = 20) -> dict[str, Any]:
        response = await self._make_request(
            "GET",
            "users",
            params={
                "query": query,
                "$top": top,
                "fields": "id,login,fullName,email",
            },
        )
        return await self._handle_response(response)


def dump(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def fail(message: str, code: int = 1) -> None:
    print(
        json.dumps({"status": "error", "message": message}, ensure_ascii=False, indent=2),
        file=sys.stderr,
    )
    raise SystemExit(code)


async def quiet_await(awaitable: Any) -> Any:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return await awaitable


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, dict):
        return first_non_empty(
            value.get("fullName"),
            value.get("name"),
            value.get("login"),
            value.get("text"),
            value.get("presentation"),
            value.get("id"),
            value,
        )
    return value


def extract_custom_field(issue: dict[str, Any], field_name: str) -> Any:
    return CustomFieldManager.extract_field_value(issue.get("customFields", []), field_name)


def build_issue_url(base_url: Optional[str], issue_id: Optional[str]) -> Optional[str]:
    if not base_url or not issue_id:
        return None
    return f"{base_url.rstrip('/')}/issue/{issue_id}"


def normalize_issue(
    issue: dict[str, Any],
    preferred_id: Optional[str] = None,
    base_url: Optional[str] = None,
) -> dict[str, Any]:
    assignee = issue.get("assignee") or {}
    normalized_id = first_non_empty(preferred_id, issue.get("idReadable"), issue.get("id"))
    normalized_custom_fields = {
        field["name"]: normalize_value(field.get("value"))
        for field in issue.get("customFields", [])
        if field.get("name")
    }
    normalized = {
        "id": normalized_id,
        "summary": issue.get("summary"),
        "description": issue.get("description"),
        "project": (issue.get("project") or {}).get("name"),
        "state": first_non_empty(extract_custom_field(issue, "State"), extract_custom_field(issue, "Status")),
        "priority": extract_custom_field(issue, "Priority"),
        "type": extract_custom_field(issue, "Type"),
        "assignee": first_non_empty(
            assignee.get("fullName"),
            assignee.get("name"),
            assignee.get("login"),
            extract_custom_field(issue, "Assignee"),
        ),
        "created": issue.get("created"),
        "updated": issue.get("updated"),
        "custom_fields": normalized_custom_fields,
    }
    issue_url = build_issue_url(base_url, normalized_id)
    if issue_url:
        normalized["url"] = issue_url
    return normalized


def normalize_board(board: dict[str, Any]) -> dict[str, Any]:
    current_sprint = board.get("currentSprint") or {}
    sprints = board.get("sprints") or []
    return {
        "id": board.get("id"),
        "name": board.get("name"),
        "projects": [project.get("name") for project in board.get("projects", [])],
        "owner": first_non_empty(
            (board.get("owner") or {}).get("fullName"),
            (board.get("owner") or {}).get("name"),
            (board.get("owner") or {}).get("login"),
        ),
        "columns": [
            column.get("presentation")
            for column in (board.get("columnSettings") or {}).get("columns", [])
        ],
        "current_sprint": current_sprint if current_sprint else None,
        "sprint_count": len(sprints),
        "sprints_disabled": (board.get("sprintsSettings") or {}).get("disableSprints"),
    }


def normalize_comment(comment: dict[str, Any]) -> dict[str, Any]:
    author = comment.get("author") or {}
    return {
        "id": comment.get("id"),
        "text": comment.get("text"),
        "created": comment.get("created"),
        "updated": comment.get("updated"),
        "author": first_non_empty(author.get("fullName"), author.get("name"), author.get("login")),
    }


async def get_issue_data(manager: IssueManager, issue_id: str) -> dict[str, Any]:
    result = await quiet_await(manager.get_issue(issue_id))
    if result["status"] != "success":
        fail(result["message"])
    return result["data"]


async def resolve_board(service: AgileService, board_ref: str) -> dict[str, Any]:
    result = await quiet_await(service.list_boards())
    if result["status"] != "success":
        fail(result["message"])

    boards = result["data"]
    exact_matches = [
        board for board in boards if board.get("id") == board_ref or board.get("name") == board_ref
    ]
    if exact_matches:
        return exact_matches[0]

    casefold_ref = board_ref.casefold()
    fuzzy_matches = [board for board in boards if (board.get("name") or "").casefold() == casefold_ref]
    if fuzzy_matches:
        return fuzzy_matches[0]

    fail(f"Board not found: {board_ref}")


def build_board_command(action: str, board_name: str, sprint_name: Optional[str] = None) -> str:
    command = f"{action} Board {board_name}"
    if sprint_name:
        command = f"{command} {sprint_name}"
    return command


def casefold_equals(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return str(left).casefold() == str(right).casefold()


def email_localpart(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    return email.split("@", 1)[0]


def read_git_email(cwd: Optional[str] = None) -> str:
    commands = [
        ["git", "config", "user.email"],
        ["git", "config", "--global", "user.email"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
        value = (result.stdout or "").strip()
        if result.returncode == 0 and value:
            return value
    fail("Could not resolve git user.email from local or global git config")


async def resolve_assignee_filter(
    auth_manager: AuthManager,
    assignee: Optional[str],
    me_from: Optional[str],
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    if not assignee:
        return None, None
    if assignee != "me":
        return assignee, None

    if me_from != "git-email-localpart":
        fail("When --assignee me is used, specify --me-from git-email-localpart")

    git_email = read_git_email()
    localpart = email_localpart(git_email)
    if not localpart:
        fail(f"Invalid git user.email: {git_email}")

    service = UserService(auth_manager)
    result = await quiet_await(service.find_users(localpart))
    if result["status"] != "success":
        fail(result["message"])
    users = result["data"] or []

    exact_login = [user for user in users if casefold_equals(user.get("login"), localpart)]
    if len(exact_login) == 1:
        return exact_login[0]["login"], {
            "source": "git-email-localpart",
            "git_email": git_email,
            "git_localpart": localpart,
            "user": exact_login[0],
        }

    exact_email_localpart = [
        user for user in users if casefold_equals(email_localpart(user.get("email")), localpart)
    ]
    if len(exact_email_localpart) == 1:
        return exact_email_localpart[0]["login"], {
            "source": "git-email-localpart",
            "git_email": git_email,
            "git_localpart": localpart,
            "user": exact_email_localpart[0],
        }

    dotted_prefix_login = [
        user
        for user in users
        if (user.get("login") or "").casefold().startswith(f"{localpart.casefold()}.")
    ]
    if len(dotted_prefix_login) == 1:
        return dotted_prefix_login[0]["login"], {
            "source": "git-email-localpart",
            "git_email": git_email,
            "git_localpart": localpart,
            "user": dotted_prefix_login[0],
        }

    candidates = [
        {
            "login": user.get("login"),
            "fullName": user.get("fullName"),
            "email": user.get("email"),
        }
        for user in users
    ]
    fail(
        "Could not uniquely resolve YouTrack user from git user.email localpart: "
        + json.dumps(
            {
                "git_email": git_email,
                "git_localpart": localpart,
                "candidates": candidates,
            },
            ensure_ascii=False,
        )
    )


def issue_matches_state(issue: dict[str, Any], state_filter: Optional[str]) -> bool:
    if not state_filter:
        return True
    normalized_state = normalize_issue(issue).get("state")
    return casefold_equals(normalized_state, state_filter)


def issue_matches_assignee(
    issue: dict[str, Any],
    assignee_filter: Optional[str],
    assignee_resolution: Optional[dict[str, Any]] = None,
) -> bool:
    if not assignee_filter:
        return True

    issue_assignee = issue.get("assignee") or {}
    candidates = [
        issue_assignee.get("id"),
        issue_assignee.get("login"),
        issue_assignee.get("fullName"),
        issue_assignee.get("name"),
        extract_custom_field(issue, "Assignee"),
    ]
    if assignee_resolution:
        resolved_user = assignee_resolution.get("user") or {}
        expected_values = [
            resolved_user.get("id"),
            resolved_user.get("login"),
            resolved_user.get("fullName"),
            email_localpart(resolved_user.get("email")),
        ]
    else:
        expected_values = [assignee_filter]
    return any(
        casefold_equals(candidate, expected_value)
        for candidate in candidates
        for expected_value in expected_values
    )


def sprint_name_from_board(board: dict[str, Any], sprint_id: Optional[str]) -> Optional[str]:
    if not sprint_id:
        return None
    current_sprint = board.get("currentSprint") or {}
    if current_sprint.get("id") == sprint_id:
        return current_sprint.get("name")
    for sprint in board.get("sprints") or []:
        if sprint.get("id") == sprint_id:
            return sprint.get("name")
    return None


async def build_board_issues_payload(
    service: AgileService,
    auth_manager: AuthManager,
    *,
    board_id: str,
    sprint_id: Optional[str],
    source: str,
    assignee: Optional[str],
    me_from: Optional[str],
    state: Optional[str],
    limit: Optional[int],
    raw: bool,
    base_url: Optional[str] = None,
) -> dict[str, Any]:
    board_result = await quiet_await(service.get_board(board_id))
    if board_result["status"] != "success":
        fail(board_result["message"])
    board = board_result["data"] or {}

    resolved_sprint_id = sprint_id
    if not resolved_sprint_id:
        current_sprint = board.get("currentSprint")
        if not current_sprint:
            fail(f"Board {board_id} has no current sprint")
        resolved_sprint_id = current_sprint["id"]

    if source == "strict":
        result = await quiet_await(service.get_sprint_issues(board_id, resolved_sprint_id))
        if result["status"] != "success":
            fail(result["message"])
        issues = result["data"]
        unresolved_issues_count = None
        sprint_name = sprint_name_from_board(board, resolved_sprint_id)
    else:
        result = await quiet_await(service.get_sprint(board_id, resolved_sprint_id, include_issues=True))
        if result["status"] != "success":
            fail(result["message"])
        sprint = result["data"] or {}
        issues = sprint.get("issues", [])
        unresolved_issues_count = sprint.get("unresolvedIssuesCount")
        sprint_name = sprint.get("name") or sprint_name_from_board(board, resolved_sprint_id)

    assignee_filter, assignee_resolution = await resolve_assignee_filter(
        auth_manager,
        assignee,
        me_from,
    )
    issues = [
        issue
        for issue in issues
        if issue_matches_assignee(issue, assignee_filter, assignee_resolution)
        and issue_matches_state(issue, state)
    ]
    if limit is not None:
        issues = issues[:limit]

    filters = {
        "assignee": assignee_filter,
        "state": state,
    }
    if assignee_resolution:
        filters["assignee_resolution"] = assignee_resolution

    payload = {
        "board_id": board_id,
        "board_name": board.get("name"),
        "sprint_id": resolved_sprint_id,
        "sprint_name": sprint_name,
        "source": source,
        "unresolved_issues_count": unresolved_issues_count,
        "filters": filters,
        "issues": issues if raw else [normalize_issue(issue, base_url=base_url) for issue in issues],
    }
    payload["issue_count"] = len(payload["issues"])
    return payload


async def run_issue_command(
    auth_manager: AuthManager,
    manager: IssueManager,
    issue_id: str,
    query: str,
    dry_run: bool = False,
    comment: Optional[str] = None,
) -> None:
    issue = await get_issue_data(manager, issue_id)
    issue_db_id = issue.get("id")
    if not issue_db_id:
        fail(f"Could not resolve database id for issue {issue_id}")

    service = CommandService(auth_manager)
    if dry_run:
        result = await quiet_await(service.assist([issue_db_id], query))
    else:
        result = await quiet_await(service.apply([issue_db_id], query, comment=comment))

    if result["status"] != "success":
        fail(result["message"])

    dump(
        {
            "status": "success",
            "issue_id": first_non_empty(issue.get("idReadable"), issue_id),
            "dry_run": dry_run,
            "query": query,
            "result": result["data"],
        }
    )


async def handle_board(
    args: argparse.Namespace,
    auth_manager: AuthManager,
    selection_label: str,
    base_url: Optional[str] = None,
) -> None:
    service = AgileService(auth_manager)

    if args.board_command == "list":
        if args.scoped:
            boards = []
            for board_id in scoped_board_ids_for_label(selection_label):
                result = await quiet_await(service.get_board(board_id))
                if result["status"] != "success":
                    fail(result["message"])
                board = result["data"] or {}
                output = normalize_board(board)
                output["sprints"] = board.get("sprints", [])
                boards.append(output)
            dump(boards)
            return
        result = await quiet_await(service.list_boards(project_id=args.project_id))
        if result["status"] != "success":
            fail(result["message"])
        boards = [normalize_board(board) for board in result["data"]]
        dump(boards)
        return

    if args.board_command == "show":
        result = await quiet_await(service.get_board(args.board_id))
        if result["status"] != "success":
            fail(result["message"])
        board = result["data"]
        output = normalize_board(board)
        output["sprints"] = board.get("sprints", [])
        dump(output)
        return

    if args.board_command == "sprints":
        if args.current:
            board_result = await quiet_await(service.get_board(args.board_id))
            if board_result["status"] != "success":
                fail(board_result["message"])
            current_sprint = (board_result["data"] or {}).get("currentSprint")
            if not current_sprint:
                fail(f"Board {args.board_id} has no current sprint")
            dump(current_sprint)
            return

        result = await quiet_await(service.list_sprints(args.board_id))
        if result["status"] != "success":
            fail(result["message"])
        dump(result["data"])
        return

    if args.board_command == "issues":
        if args.mine and args.assignee:
            fail("Use either --mine or --assignee, not both")

        assignee_arg = "me" if args.mine else args.assignee
        me_from_arg = args.me_from or ("git-email-localpart" if args.mine else None)
        dump(
            await build_board_issues_payload(
                service,
                auth_manager,
                board_id=args.board_id,
                sprint_id=args.sprint_id,
                source=args.source,
                assignee=assignee_arg,
                me_from=me_from_arg,
                state=args.state,
                limit=args.limit,
                raw=args.raw,
                base_url=base_url,
            )
        )
        return

    if args.board_command == "scoped-issues":
        if args.mine and args.assignee:
            fail("Use either --mine or --assignee, not both")

        scoped_board_ids = scoped_board_ids_for_label(selection_label)
        if not scoped_board_ids:
            fail(
                "No scoped board ids are configured for this instance. "
                "Re-login with '--board-id <id>' or run "
                f"'yt instances scope set {selection_label} <board-id> [<board-id> ...]'."
            )

        assignee_arg = "me" if args.mine else args.assignee
        me_from_arg = args.me_from or ("git-email-localpart" if args.mine else None)
        boards = []
        for board_id in scoped_board_ids:
            boards.append(
                await build_board_issues_payload(
                    service,
                    auth_manager,
                    board_id=board_id,
                    sprint_id=None,
                    source=args.source,
                    assignee=assignee_arg,
                    me_from=me_from_arg,
                    state=args.state,
                    limit=args.limit,
                    raw=args.raw,
                    base_url=base_url,
                )
            )
        dump(
            {
                "scoped_board_ids": scoped_board_ids,
                "source": args.source,
                "board_count": len(boards),
                "boards": boards,
            }
        )
        return

    fail(f"Unknown board command: {args.board_command}")


def parse_custom_fields(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            fail(f"Invalid custom field format: {item}. Expected Name=Value")
        key, value = item.split("=", 1)
        result[key] = value
    return result


async def handle_issue(
    args: argparse.Namespace,
    auth_manager: AuthManager,
    base_url: Optional[str] = None,
) -> None:
    manager = IssueManager(auth_manager)
    board_service = AgileService(auth_manager)

    if args.issue_command == "command":
        await run_issue_command(
            auth_manager=auth_manager,
            manager=manager,
            issue_id=args.issue_id,
            query=args.query,
            dry_run=args.dry_run,
            comment=args.comment,
        )
        return

    if args.issue_command in {"board-add", "board-remove"}:
        if args.sprint and args.current_sprint:
            fail("Use either --sprint or --current-sprint, not both")

        board = await resolve_board(board_service, args.board)
        board_name = board.get("name")
        sprint_name = args.sprint

        if args.current_sprint:
            current_sprint = board.get("currentSprint")
            if not current_sprint or not current_sprint.get("name"):
                fail(f"Board {board_name} has no current sprint")
            sprint_name = current_sprint["name"]

        query = build_board_command(
            "add" if args.issue_command == "board-add" else "remove",
            board_name=board_name,
            sprint_name=sprint_name,
        )
        await run_issue_command(
            auth_manager=auth_manager,
            manager=manager,
            issue_id=args.issue_id,
            query=query,
            dry_run=args.dry_run,
            comment=args.comment,
        )
        return

    if args.issue_command == "show":
        issue = await get_issue_data(manager, args.issue_id)
        if args.raw:
            dump(issue)
        else:
            dump(normalize_issue(issue, preferred_id=args.issue_id, base_url=base_url))
        return

    if args.issue_command == "search":
        result = await quiet_await(
            manager.search_issues(
                query=args.query,
                project_id=args.project_id,
                format_output="json",
            )
        )
        if result["status"] != "success":
            fail(result["message"])
        dump([normalize_issue(issue, base_url=base_url) for issue in result["data"]])
        return

    if args.issue_command == "update":
        custom_fields = parse_custom_fields(args.custom_field or [])
        has_change = any(
            value is not None
            for value in [
                args.summary,
                args.description,
                args.state,
                args.priority,
                args.assignee,
                args.type,
            ]
        ) or bool(custom_fields)
        if not has_change:
            fail("No issue changes specified")

        result = await quiet_await(
            manager.update_issue(
                issue_id=args.issue_id,
                summary=args.summary,
                description=args.description,
                state=args.state,
                priority=args.priority,
                assignee=args.assignee,
                issue_type=args.type,
                custom_fields=custom_fields or None,
            )
        )
        dump(result)
        return

    if args.issue_command == "comment-add":
        result = await quiet_await(manager.add_comment(args.issue_id, args.text))
        dump(result)
        return

    if args.issue_command == "comment-list":
        result = await quiet_await(manager.list_comments(args.issue_id))
        if result["status"] != "success":
            fail(result["message"])
        dump([normalize_comment(comment) for comment in result["data"]])
        return

    if args.issue_command == "comment-update":
        result = await quiet_await(manager.update_comment(args.issue_id, args.comment_id, args.text))
        dump(result)
        return

    if args.issue_command == "comment-delete":
        result = await quiet_await(manager.delete_comment(args.issue_id, args.comment_id))
        dump(result)
        return

    fail(f"Unknown issue command: {args.issue_command}")


async def handle_instances(skill_dir: Path, args: argparse.Namespace) -> None:
    if args.instances_command == "list":
        dump(instances_list_payload(skill_dir, args.instance))
        return
    if args.instances_command == "current":
        dump(instances_current_payload(skill_dir, args.instance))
        return
    if args.instances_command == "use":
        dump(use_instance(skill_dir, args.label))
        return
    fail(f"Unknown instances command: {args.instances_command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Agent-friendly YouTrack helper on top of yt-cli credentials"
    )
    parser.add_argument("--instance", help="Named YouTrack instance label")
    subparsers = parser.add_subparsers(dest="command", required=True)

    instances = subparsers.add_parser("instances", help="Manage named YouTrack instances")
    instances_subparsers = instances.add_subparsers(dest="instances_command", required=True)
    instances_subparsers.add_parser("list", help="List known YouTrack instances")
    instances_subparsers.add_parser("current", help="Show the selected/current YouTrack instance")
    instances_use = instances_subparsers.add_parser("use", help="Pin an instance as active")
    instances_use.add_argument("label")

    board = subparsers.add_parser("board", help="Agile board operations")
    board_subparsers = board.add_subparsers(dest="board_command", required=True)

    board_list = board_subparsers.add_parser("list", help="List boards")
    board_list.add_argument("--project-id")
    board_list.add_argument(
        "--scoped",
        action="store_true",
        help="Restrict results to scoped board ids configured for the selected instance",
    )

    board_show = board_subparsers.add_parser("show", help="Show board details")
    board_show.add_argument("board_id")

    board_sprints = board_subparsers.add_parser("sprints", help="List board sprints")
    board_sprints.add_argument("board_id")
    board_sprints.add_argument("--current", action="store_true")

    board_issues = board_subparsers.add_parser("issues", help="List sprint issues for a board")
    board_issues.add_argument("board_id")
    board_issues.add_argument("--sprint-id")
    board_issues.add_argument("--source", choices=["web", "strict"], default="web")
    board_issues.add_argument("--assignee", help="Assignee full name/login or 'me'")
    board_issues.add_argument(
        "--mine",
        action="store_true",
        help="Alias for --assignee me --me-from git-email-localpart",
    )
    board_issues.add_argument("--me-from", choices=["git-email-localpart"])
    board_issues.add_argument("--state", help="Exact state name, for example 'In Progress'")
    board_issues.add_argument("--limit", type=int)
    board_issues.add_argument("--raw", action="store_true")

    board_scoped_issues = board_subparsers.add_parser(
        "scoped-issues",
        help="List current sprint issues across the scoped board ids for the selected instance",
    )
    board_scoped_issues.add_argument("--source", choices=["web", "strict"], default="web")
    board_scoped_issues.add_argument("--assignee", help="Assignee full name/login or 'me'")
    board_scoped_issues.add_argument(
        "--mine",
        action="store_true",
        help="Alias for --assignee me --me-from git-email-localpart",
    )
    board_scoped_issues.add_argument("--me-from", choices=["git-email-localpart"])
    board_scoped_issues.add_argument("--state", help="Exact state name, for example 'In Progress'")
    board_scoped_issues.add_argument("--limit", type=int, help="Per-board issue limit")
    board_scoped_issues.add_argument("--raw", action="store_true")

    issue = subparsers.add_parser("issue", help="Issue operations")
    issue_subparsers = issue.add_subparsers(dest="issue_command", required=True)

    issue_show = issue_subparsers.add_parser("show", help="Show issue details")
    issue_show.add_argument("issue_id")
    issue_show.add_argument("--raw", action="store_true")

    issue_command = issue_subparsers.add_parser(
        "command",
        help="Apply raw YouTrack command to an issue",
    )
    issue_command.add_argument("issue_id")
    issue_command.add_argument("query")
    issue_command.add_argument("--comment")
    issue_command.add_argument("--dry-run", action="store_true")

    issue_board_add = issue_subparsers.add_parser(
        "board-add",
        help="Add issue to a board and optional sprint",
    )
    issue_board_add.add_argument("issue_id")
    issue_board_add.add_argument("--board", required=True, help="Board id or exact board name")
    issue_board_add.add_argument("--sprint", help="Exact sprint name")
    issue_board_add.add_argument("--current-sprint", action="store_true")
    issue_board_add.add_argument("--comment")
    issue_board_add.add_argument("--dry-run", action="store_true")

    issue_board_remove = issue_subparsers.add_parser(
        "board-remove",
        help="Remove issue from a board and optional sprint",
    )
    issue_board_remove.add_argument("issue_id")
    issue_board_remove.add_argument("--board", required=True, help="Board id or exact board name")
    issue_board_remove.add_argument("--sprint", help="Exact sprint name")
    issue_board_remove.add_argument("--current-sprint", action="store_true")
    issue_board_remove.add_argument("--comment")
    issue_board_remove.add_argument("--dry-run", action="store_true")

    issue_search = issue_subparsers.add_parser("search", help="Search issues")
    issue_search.add_argument("query")
    issue_search.add_argument("--project-id")

    issue_update = issue_subparsers.add_parser("update", help="Update issue fields")
    issue_update.add_argument("issue_id")
    issue_update.add_argument("--summary")
    issue_update.add_argument("--description")
    issue_update.add_argument("--state")
    issue_update.add_argument("--priority")
    issue_update.add_argument("--assignee")
    issue_update.add_argument("--type")
    issue_update.add_argument("--custom-field", action="append")

    comment_add = issue_subparsers.add_parser("comment-add", help="Add issue comment")
    comment_add.add_argument("issue_id")
    comment_add.add_argument("text")

    comment_list = issue_subparsers.add_parser("comment-list", help="List issue comments")
    comment_list.add_argument("issue_id")

    comment_update = issue_subparsers.add_parser("comment-update", help="Update issue comment")
    comment_update.add_argument("issue_id")
    comment_update.add_argument("comment_id")
    comment_update.add_argument("text")

    comment_delete = issue_subparsers.add_parser("comment-delete", help="Delete issue comment")
    comment_delete.add_argument("issue_id")
    comment_delete.add_argument("comment_id")

    return parser


async def main_async() -> None:
    parser = build_parser()
    args = parser.parse_args()
    skill_dir = Path(__file__).resolve().parent.parent

    try:
        if args.command == "instances":
            await handle_instances(skill_dir, args)
            return
        if args.command == "board":
            with activated_auth_manager(skill_dir, args.instance, require_ready=True) as (_, selection, auth_manager):
                await handle_board(
                    args,
                    auth_manager,
                    selection.label,
                    base_url=base_url_for_label(selection.label),
                )
            return
        if args.command == "issue":
            with activated_auth_manager(skill_dir, args.instance, require_ready=True) as (_, selection, auth_manager):
                await handle_issue(args, auth_manager, base_url=base_url_for_label(selection.label))
            return
        fail(f"Unknown command: {args.command}")
    except InstanceRuntimeError as exc:
        fail(str(exc))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

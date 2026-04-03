#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import re
import subprocess
import sys
from collections import defaultdict
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Optional

logging.disable(logging.CRITICAL)

from youtrack_cli.auth import AuthManager
from youtrack_cli.custom_field_manager import CustomFieldManager
from youtrack_cli.custom_field_types import CustomFieldValueTypes, IssueCustomFieldTypes
from youtrack_cli.managers.issues import IssueManager
from youtrack_cli.services.base import BaseService
from youtrack_cli.services.issues import IssueService
from youtrack_cli.services.projects import ProjectService

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
        "id,idReadable,summary,created,updated,resolved,"
        "project(id,name),"
        "assignee(id,login,name,fullName),"
        "customFields(name,value(id,login,name,fullName,text,presentation))"
    )

    async def list_boards(self, project_id: Optional[str] = None) -> dict[str, Any]:
        params = {
            "$top": 2000,
            "fields": (
                "id,name,"
                "projects(id,name,shortName),"
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
                "projects(id,name,shortName),"
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


class WorkflowIssueService(BaseService):
    async def create_issue(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._make_request("POST", "issues", json_data=payload)
        return await self._handle_response(response, success_codes=[200, 201])

    async def update_issue(self, issue_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._make_request("POST", f"issues/{issue_id}", json_data=payload)
        return await self._handle_response(response, success_codes=[200, 204])


PROJECT_LOOKUP_FIELDS = "id,shortName,name"
PROJECT_FIELD_LIST_FIELDS = "id,canBeEmpty,emptyFieldText,$type,field(name,fieldType,$type)"
ISSUE_BRIEF_FIELDS = (
    "id,idReadable,summary,description,created,updated,resolved,"
    "project(id,name,shortName),"
    "assignee(id,login,name,fullName),"
    "links(direction,linkType(id,name,directed,sourceToTarget,targetToSource),issues(id,idReadable,summary)),"
    "customFields(name,value(id,login,name,fullName,text,presentation))"
)
ISSUE_FIELD_SNAPSHOT_FIELDS = (
    "id,idReadable,"
    "customFields(name,$type,value(id,login,name,fullName,text,presentation,$type))"
)
SINGLE_VALUE_FIELD_TYPES = {
    IssueCustomFieldTypes.SINGLE_ENUM,
    IssueCustomFieldTypes.STATE,
    IssueCustomFieldTypes.SINGLE_USER,
    IssueCustomFieldTypes.SINGLE_VERSION,
    IssueCustomFieldTypes.TEXT,
    IssueCustomFieldTypes.PERIOD,
    IssueCustomFieldTypes.INTEGER,
}
MULTI_VALUE_FIELD_TYPES = {
    IssueCustomFieldTypes.MULTI_ENUM,
    IssueCustomFieldTypes.MULTI_USER,
    IssueCustomFieldTypes.MULTI_VERSION,
}
COMMAND_DRY_RUN_HINT = "Run without --apply for preview, then rerun with --apply to mutate."
INTERNAL_ID_PATTERN = re.compile(r"^\d+-\d+$")


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


def build_board_url(base_url: Optional[str], board_id: Optional[str]) -> Optional[str]:
    if not base_url or not board_id:
        return None
    return f"{base_url.rstrip('/')}/agiles/{board_id}/current"


def normalize_issue(
    issue: dict[str, Any],
    preferred_id: Optional[str] = None,
    base_url: Optional[str] = None,
    include_description: bool = True,
    include_custom_fields: bool = True,
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
        "initiator": extract_custom_field(issue, "Initiator"),
        "resolved": issue.get("resolved"),
        "created": issue.get("created"),
        "updated": issue.get("updated"),
    }
    if include_description:
        normalized["description"] = issue.get("description")
    if include_custom_fields:
        normalized["custom_fields"] = normalized_custom_fields
    issue_url = build_issue_url(base_url, normalized_id)
    if issue_url:
        normalized["url"] = issue_url
    return normalized


def normalize_issue_link(link: dict[str, Any], base_url: Optional[str] = None) -> dict[str, Any]:
    link_type = link.get("linkType") or {}
    issues = []
    for linked_issue in link.get("issues", []):
        issue_id = first_non_empty(linked_issue.get("idReadable"), linked_issue.get("id"))
        normalized_issue = {
            "id": issue_id,
            "summary": linked_issue.get("summary"),
        }
        issue_url = build_issue_url(base_url, issue_id)
        if issue_url:
            normalized_issue["url"] = issue_url
        issues.append(normalized_issue)
    return {
        "type": link_type.get("name"),
        "direction": link.get("direction"),
        "source_to_target": link_type.get("sourceToTarget"),
        "target_to_source": link_type.get("targetToSource"),
        "issues": issues,
    }


def normalize_board(board: dict[str, Any], base_url: Optional[str] = None) -> dict[str, Any]:
    current_sprint = board.get("currentSprint") or {}
    sprints = board.get("sprints") or []
    normalized = {
        "id": board.get("id"),
        "name": board.get("name"),
        "projects": [project.get("name") for project in board.get("projects", [])],
        "project_refs": [
            {
                "id": project.get("id"),
                "name": project.get("name"),
                "shortName": project.get("shortName"),
            }
            for project in board.get("projects", [])
        ],
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
    board_url = build_board_url(base_url, board.get("id"))
    if board_url:
        normalized["url"] = board_url
    return normalized


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


def normalize_user_candidate(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user.get("id"),
        "login": user.get("login"),
        "fullName": user.get("fullName"),
        "email": user.get("email"),
    }


async def resolve_me_user(auth_manager: AuthManager, me_from: Optional[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    if me_from != "git-email-localpart":
        fail("When 'me' is used, specify --me-from git-email-localpart")

    git_email = read_git_email()
    localpart = email_localpart(git_email)
    if not localpart:
        fail(f"Invalid git user.email: {git_email}")

    service = UserService(auth_manager)
    result = await quiet_await(service.find_users(localpart))
    if result["status"] != "success":
        fail(result["message"])
    users = result["data"] or []

    for matcher in (
        lambda user: casefold_equals(user.get("login"), localpart),
        lambda user: casefold_equals(email_localpart(user.get("email")), localpart),
        lambda user: (user.get("login") or "").casefold().startswith(f"{localpart.casefold()}."),
    ):
        matches = [user for user in users if matcher(user)]
        if len(matches) == 1:
            return matches[0], {
                "source": "git-email-localpart",
                "git_email": git_email,
                "git_localpart": localpart,
                "user": normalize_user_candidate(matches[0]),
            }

    fail(
        "Could not uniquely resolve YouTrack user from git user.email localpart: "
        + json.dumps(
            {
                "git_email": git_email,
                "git_localpart": localpart,
                "candidates": [normalize_user_candidate(user) for user in users],
            },
            ensure_ascii=False,
        )
    )


async def resolve_user_reference(
    auth_manager: AuthManager,
    user_ref: str,
    *,
    allow_me: bool = False,
    me_from: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    normalized_ref = (user_ref or "").strip()
    if not normalized_ref:
        fail("User reference cannot be empty")

    if normalized_ref == "me":
        if not allow_me:
            fail("'me' is not supported for this option")
        user, resolution = await resolve_me_user(auth_manager, me_from)
        return user["login"], resolution

    service = UserService(auth_manager)
    result = await quiet_await(service.find_users(normalized_ref))
    if result["status"] != "success":
        fail(result["message"])
    users = result["data"] or []

    def exact_match(user: dict[str, Any]) -> bool:
        return any(
            casefold_equals(candidate, normalized_ref)
            for candidate in [
                user.get("login"),
                user.get("fullName"),
                user.get("name"),
                user.get("email"),
                email_localpart(user.get("email")),
            ]
        )

    matches = [user for user in users if exact_match(user)]
    if len(matches) == 1:
        return matches[0]["login"], {
            "source": "user-search",
            "query": normalized_ref,
            "user": normalize_user_candidate(matches[0]),
        }

    if len(users) == 1 and users[0].get("login"):
        return users[0]["login"], {
            "source": "user-search",
            "query": normalized_ref,
            "user": normalize_user_candidate(users[0]),
        }

    fail(
        "Could not uniquely resolve YouTrack user reference: "
        + json.dumps(
            {
                "query": normalized_ref,
                "candidates": [normalize_user_candidate(user) for user in users],
            },
            ensure_ascii=False,
        )
    )


async def resolve_assignee_filter(
    auth_manager: AuthManager,
    assignee: Optional[str],
    me_from: Optional[str],
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    if not assignee:
        return None, None
    if assignee != "me":
        return assignee, None
    login, resolution = await resolve_user_reference(
        auth_manager,
        "me",
        allow_me=True,
        me_from=me_from,
    )
    return login, resolution


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


def issue_matches_initiator(issue: dict[str, Any], initiator_filter: Optional[str]) -> bool:
    if not initiator_filter:
        return True
    initiator = extract_custom_field(issue, "Initiator")
    return casefold_equals(initiator, initiator_filter)


def issue_matches_active(issue: dict[str, Any], active_only: bool) -> bool:
    if not active_only:
        return True
    return issue.get("resolved") in (None, "")


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


async def fetch_board(service: AgileService, board_id: str) -> dict[str, Any]:
    result = await quiet_await(service.get_board(board_id))
    if result["status"] != "success":
        fail(result["message"])
    return result["data"] or {}


async def load_scoped_boards(service: AgileService, selection_label: str) -> tuple[list[str], list[dict[str, Any]]]:
    scoped_ids = scoped_board_ids_for_label(selection_label)
    boards = []
    for board_id in scoped_ids:
        boards.append(await fetch_board(service, board_id))
    return scoped_ids, boards


def find_board_match(boards: list[dict[str, Any]], board_ref: str) -> Optional[dict[str, Any]]:
    exact_matches = [
        board
        for board in boards
        if board.get("id") == board_ref or board.get("name") == board_ref
    ]
    if exact_matches:
        return exact_matches[0]

    ref_casefold = board_ref.casefold()
    for board in boards:
        if (board.get("name") or "").casefold() == ref_casefold:
            return board
    return None


async def resolve_target_board(
    service: AgileService,
    selection_label: str,
    board_ref: Optional[str],
) -> tuple[dict[str, Any], list[str]]:
    scoped_ids, scoped_boards = await load_scoped_boards(service, selection_label)
    if board_ref:
        if scoped_ids:
            board = find_board_match(scoped_boards, board_ref)
            if not board:
                fail(
                    f"Board '{board_ref}' is not in the scoped boards for instance '{selection_label}'."
                )
            return board, scoped_ids
        board = await resolve_board(service, board_ref)
        return await fetch_board(service, board["id"]), scoped_ids

    if len(scoped_boards) == 1:
        return scoped_boards[0], scoped_ids
    if len(scoped_boards) > 1:
        fail(
            "Multiple scoped boards are configured for this instance. "
            "Specify --board <id-or-name>."
        )
    fail(
        "No board specified and no scoped boards are configured for this instance. "
        "Pass --board <id-or-name> or configure scoped boards."
    )


def infer_project_ref_from_board(board: dict[str, Any]) -> Optional[str]:
    projects = board.get("projects") or []
    if len(projects) != 1:
        return None
    project = projects[0] or {}
    return first_non_empty(project.get("shortName"), project.get("id"), project.get("name"))


async def resolve_project_context(
    auth_manager: AuthManager,
    project_ref: str,
) -> dict[str, Any]:
    service = ProjectService(auth_manager)
    result = await quiet_await(service.get_project(project_ref, fields=PROJECT_LOOKUP_FIELDS))
    if result["status"] != "success":
        fail(result["message"])
    project = result["data"] or {}
    if not project.get("id"):
        fail(f"Could not resolve project: {project_ref}")
    return project


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
        "issues": (
            issues
            if raw
            else [
                normalize_issue(
                    issue,
                    base_url=base_url,
                    include_description=False,
                    include_custom_fields=False,
                )
                for issue in issues
            ]
        ),
    }
    board_url = build_board_url(base_url, board_id)
    if board_url:
        payload["board_url"] = board_url
    payload["issue_count"] = len(payload["issues"])
    return payload


async def build_board_tasks_payload(
    service: AgileService,
    auth_manager: AuthManager,
    *,
    selection_label: str,
    board_ref: Optional[str],
    source: str,
    assignee: Optional[str],
    me_from: Optional[str],
    initiator: Optional[str],
    state: Optional[str],
    active_only: bool,
    limit: Optional[int],
    base_url: Optional[str],
) -> dict[str, Any]:
    board, scoped_ids = await resolve_target_board(service, selection_label, board_ref)
    payload = await build_board_issues_payload(
        service,
        auth_manager,
        board_id=board["id"],
        sprint_id=None,
        source=source,
        assignee=assignee,
        me_from=me_from,
        state=state,
        limit=None,
        raw=True,
        base_url=base_url,
    )

    issues = payload["issues"]
    issues = [
        issue
        for issue in issues
        if issue_matches_initiator(issue, initiator)
        and issue_matches_active(issue, active_only)
    ]
    if limit is not None:
        issues = issues[:limit]

    assignee_filter = payload.get("filters", {}).get("assignee")
    output = {
        "board_id": board.get("id"),
        "board_name": board.get("name"),
        "sprint_id": payload.get("sprint_id"),
        "sprint_name": payload.get("sprint_name"),
        "source": source,
        "filters": {
            "assignee": assignee_filter,
            "initiator": initiator,
            "state": state,
            "active_only": active_only,
        },
        "issues": [
            normalize_issue(
                issue,
                base_url=base_url,
                include_description=False,
                include_custom_fields=False,
            )
            for issue in issues
        ],
    }
    if scoped_ids:
        output["scoped_board_ids"] = scoped_ids
    board_url = build_board_url(base_url, board.get("id"))
    if board_url:
        output["board_url"] = board_url
    assignee_resolution = payload.get("filters", {}).get("assignee_resolution")
    if assignee_resolution:
        output["filters"]["assignee_resolution"] = assignee_resolution
    output["issue_count"] = len(output["issues"])
    return output


async def build_issue_brief_payload(
    issue_service: IssueService,
    issue_id: str,
    base_url: Optional[str],
) -> dict[str, Any]:
    result = await quiet_await(issue_service.get_issue(issue_id, fields=ISSUE_BRIEF_FIELDS))
    if result["status"] != "success":
        fail(result["message"])
    issue = result["data"] or {}
    payload = normalize_issue(issue, preferred_id=issue_id, base_url=base_url)
    payload["links"] = [normalize_issue_link(link, base_url=base_url) for link in issue.get("links", [])]
    return payload


async def run_issue_command(
    auth_manager: AuthManager,
    manager: IssueManager,
    issue_id: str,
    query: str,
    dry_run: bool = False,
    comment: Optional[str] = None,
) -> None:
    dump(
        await execute_issue_command(
            auth_manager=auth_manager,
            manager=manager,
            issue_id=issue_id,
            query=query,
            dry_run=dry_run,
            comment=comment,
        )
    )


async def prepare_issue_create_operation(
    auth_manager: AuthManager,
    *,
    selection_label: str,
    summary: str,
    description: Optional[str],
    project_ref: Optional[str],
    board_ref: Optional[str],
    use_current_sprint: bool,
    parent_issue_id: Optional[str],
    type_name: Optional[str],
    priority: Optional[str],
    assignee: Optional[str],
    mine: bool,
    me_from: Optional[str],
    raw_fields: list[str],
) -> dict[str, Any]:
    board_service = AgileService(auth_manager)
    board = None
    sprint_name = None
    scoped_board_ids: list[str] = []
    if board_ref or use_current_sprint:
        board, scoped_board_ids = await resolve_target_board(board_service, selection_label, board_ref)
        if use_current_sprint:
            current_sprint = board.get("currentSprint") or {}
            if not current_sprint.get("name"):
                fail(f"Board {board.get('name') or board.get('id')} has no current sprint")
            sprint_name = current_sprint["name"]

    effective_project_ref = project_ref
    if not effective_project_ref and board:
        effective_project_ref = infer_project_ref_from_board(board)
        if not effective_project_ref:
            fail(
                "The selected board is attached to multiple projects. "
                "Specify --project explicitly."
            )
    if not effective_project_ref:
        fail("Missing project. Use --project or provide --board for a single-project board.")

    project = await resolve_project_context(auth_manager, effective_project_ref)

    if mine and assignee:
        fail("Use either --mine or --assignee, not both")

    resolved_assignee = None
    if mine:
        resolved_assignee, _ = await resolve_user_reference(
            auth_manager,
            "me",
            allow_me=True,
            me_from=me_from or "git-email-localpart",
        )
    elif assignee:
        resolved_assignee, _ = await resolve_user_reference(auth_manager, assignee)

    field_assignments = parse_field_assignments(raw_fields)
    add_single_field_assignment(field_assignments, "Type", type_name, "--type")
    add_single_field_assignment(field_assignments, "Priority", priority, "--priority")
    add_single_field_assignment(field_assignments, "Assignee", resolved_assignee, "--assignee/--mine")

    project_lookup_ref = first_non_empty(project.get("shortName"), project.get("id"))
    existing_issue_field_shapes: dict[str, dict[str, Any]] = {}
    existing_issue_field_source = None
    if field_assignments:
        (
            existing_issue_field_shapes,
            _existing_issue_fields,
            existing_issue_field_source,
        ) = await resolve_existing_issue_field_context(
            auth_manager,
            parent_issue_id=parent_issue_id,
            project_lookup_ref=project_lookup_ref,
        )

    custom_field_payloads, field_previews, resolved_field_infos = await build_project_field_payloads(
        auth_manager,
        project_lookup_ref,
        field_assignments,
        existing_issue_field_shapes=existing_issue_field_shapes,
    )

    issue_payload: dict[str, Any] = {
        "project": {"id": project["id"]},
        "summary": summary,
    }
    if description:
        issue_payload["description"] = description
    if custom_field_payloads:
        issue_payload["customFields"] = custom_field_payloads

    preview = build_issue_mutation_preview(
        operation="create-subtask" if parent_issue_id else "create-issue",
        summary=summary,
        description=description,
        project={
            "id": project.get("id"),
            "shortName": project.get("shortName"),
            "name": project.get("name"),
        },
        parent_issue_id=parent_issue_id,
        board=board,
        sprint_name=sprint_name,
        field_previews=field_previews,
        dry_run=True,
    )
    if scoped_board_ids:
        preview["target"]["scoped_board_ids"] = scoped_board_ids
    preview["hint"] = COMMAND_DRY_RUN_HINT

    return {
        "project": project,
        "board": board,
        "sprint_name": sprint_name,
        "parent_issue_id": parent_issue_id,
        "issue_payload": issue_payload,
        "preview": preview,
        "field_infos": resolved_field_infos,
        "existing_issue_field_shapes": existing_issue_field_shapes,
        "existing_issue_field_source": existing_issue_field_source,
    }


async def apply_issue_create_operation(
    auth_manager: AuthManager,
    prepared: dict[str, Any],
    *,
    base_url: Optional[str],
) -> dict[str, Any]:
    workflow_service = WorkflowIssueService(auth_manager)
    issue_service = IssueService(auth_manager)
    issue_manager = IssueManager(auth_manager)

    try:
        create_result = await quiet_await(workflow_service.create_issue(prepared["issue_payload"]))
    except Exception as error:
        return await build_issue_create_error_payload(auth_manager, prepared, error)
    if create_result["status"] != "success":
        return await build_issue_create_error_payload(auth_manager, prepared, create_result)
    created = create_result["data"] or {}
    created_id = first_non_empty(created.get("idReadable"), created.get("id"))
    if not created_id:
        fail("Created issue did not include an id")

    created_issue = await build_issue_brief_payload(issue_service, created_id, base_url)
    applied_actions = [{"type": "create_issue", "issue_id": created_issue["id"]}]

    if prepared.get("parent_issue_id"):
        link_result = await quiet_await(
            issue_service.create_link(
                created_issue["id"],
                prepared["parent_issue_id"],
                "Subtask",
            )
        )
        if link_result["status"] != "success":
            return {
                "status": "partial_success",
                "dry_run": False,
                "operation": "create-subtask",
                "created_issue": created_issue,
                "applied_actions": applied_actions,
                "failed_action": {
                    "type": "link_issue",
                    "link_type": "Subtask",
                    "target_issue_id": prepared["parent_issue_id"],
                    "message": link_result["message"],
                },
                "warnings": [],
            }
        applied_actions.append(
            {
                "type": "link_issue",
                "link_type": "Subtask",
                "target_issue_id": prepared["parent_issue_id"],
            }
        )

    board = prepared.get("board")
    if board:
        query = build_board_command("add", board["name"], prepared.get("sprint_name"))
        board_add_result = await execute_issue_command(
            auth_manager,
            issue_manager,
            created_issue["id"],
            query,
            dry_run=False,
            raise_on_error=False,
        )
        if board_add_result["status"] != "success":
            return {
                "status": "partial_success",
                "dry_run": False,
                "operation": "create-subtask" if prepared.get("parent_issue_id") else "create-issue",
                "created_issue": created_issue,
                "applied_actions": applied_actions,
                "failed_action": {
                    "type": "board_add",
                    "board_id": board.get("id"),
                    "board_name": board.get("name"),
                    "sprint_name": prepared.get("sprint_name"),
                    "message": board_add_result["message"],
                },
                "warnings": [],
            }
        applied_actions.append(
            {
                "type": "board_add",
                "board_id": board.get("id"),
                "board_name": board.get("name"),
                "sprint_name": prepared.get("sprint_name"),
            }
        )

    return {
        "status": "success",
        "dry_run": False,
        "operation": "create-subtask" if prepared.get("parent_issue_id") else "create-issue",
        "created_issue": created_issue,
        "applied_actions": applied_actions,
        "warnings": [],
    }


async def preview_or_apply_issue_link(
    auth_manager: AuthManager,
    *,
    source_issue_id: str,
    target_issue_id: str,
    link_type: str,
    apply: bool,
) -> dict[str, Any]:
    if not apply:
        return {
            "status": "success",
            "dry_run": True,
            "operation": "link-issue",
            "planned_actions": [
                {
                    "type": "link_issue",
                    "source_issue_id": source_issue_id,
                    "target_issue_id": target_issue_id,
                    "link_type": link_type,
                }
            ],
            "warnings": [],
            "validation_errors": [],
            "hint": COMMAND_DRY_RUN_HINT,
        }

    service = IssueService(auth_manager)
    result = await quiet_await(service.create_link(source_issue_id, target_issue_id, link_type))
    if result["status"] != "success":
        fail(result["message"])
    return {
        "status": "success",
        "dry_run": False,
        "operation": "link-issue",
        "applied_actions": [
            {
                "type": "link_issue",
                "source_issue_id": source_issue_id,
                "target_issue_id": target_issue_id,
                "link_type": link_type,
            }
        ],
        "warnings": [],
    }


async def handle_board(
    args: argparse.Namespace,
    auth_manager: AuthManager,
    selection_label: str,
    base_url: Optional[str] = None,
) -> None:
    service = AgileService(auth_manager)

    if args.board_command == "current":
        board, scoped_ids = await resolve_target_board(service, selection_label, args.board)
        output = normalize_board(board, base_url=base_url)
        if scoped_ids:
            output["scoped_board_ids"] = scoped_ids
        dump(output)
        return

    if args.board_command == "list":
        if args.scoped:
            _, scoped_boards = await load_scoped_boards(service, selection_label)
            boards = []
            for board in scoped_boards:
                output = normalize_board(board, base_url=base_url)
                output["sprints"] = board.get("sprints", [])
                boards.append(output)
            dump(boards)
            return
        result = await quiet_await(service.list_boards(project_id=args.project_id))
        if result["status"] != "success":
            fail(result["message"])
        boards = [normalize_board(board, base_url=base_url) for board in result["data"]]
        dump(boards)
        return

    if args.board_command == "show":
        result = await quiet_await(service.get_board(args.board_id))
        if result["status"] != "success":
            fail(result["message"])
        board = result["data"]
        output = normalize_board(board, base_url=base_url)
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

    if args.board_command in {"tasks", "my-tasks"}:
        if args.board_command == "my-tasks":
            if getattr(args, "assignee", None) or getattr(args, "initiator", None):
                fail("board my-tasks does not accept --assignee or --initiator")
            assignee_arg = "me"
            me_from_arg = "git-email-localpart"
        else:
            if args.mine and args.assignee:
                fail("Use either --mine or --assignee, not both")
            assignee_arg = "me" if args.mine else args.assignee
            me_from_arg = args.me_from or ("git-email-localpart" if args.mine else None)

        dump(
            await build_board_tasks_payload(
                service,
                auth_manager,
                selection_label=selection_label,
                board_ref=args.board,
                source=args.source,
                assignee=assignee_arg,
                me_from=me_from_arg,
                initiator=getattr(args, "initiator", None),
                state=args.state,
                active_only=args.active_only,
                limit=args.limit,
                base_url=base_url,
            )
        )
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

    if args.board_command in {"create-task", "create-subtask"}:
        prepared = await prepare_issue_create_operation(
            auth_manager,
            selection_label=selection_label,
            summary=args.summary,
            description=args.description,
            project_ref=args.project,
            board_ref=args.board,
            use_current_sprint=args.current_sprint,
            parent_issue_id=getattr(args, "parent_issue_id", None),
            type_name=args.type,
            priority=args.priority,
            assignee=args.assignee,
            mine=args.mine,
            me_from=args.me_from,
            raw_fields=args.field or [],
        )
        if not args.apply:
            dump(prepared["preview"])
            return
        dump(await apply_issue_create_operation(auth_manager, prepared, base_url=base_url))
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


def parse_field_assignments(values: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for item in values:
        if "=" not in item:
            fail(f"Invalid field format: {item}. Expected Name=Value")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            fail(f"Invalid field format: {item}. Field name cannot be empty")
        result[key].append(value)
    return dict(result)


def add_single_field_assignment(
    assignments: dict[str, list[str]],
    field_name: str,
    value: Optional[str],
    option_name: str,
) -> None:
    if value is None:
        return
    if field_name in assignments:
        fail(f"Use either {option_name} or --field '{field_name}=...', not both")
    assignments[field_name] = [value]


def field_preview_is_required(field_info: dict[str, Any]) -> bool:
    can_be_empty = field_info.get("can_be_empty")
    if can_be_empty is None:
        field_details = field_info.get("field_details") or {}
        if "canBeEmpty" in field_details:
            can_be_empty = field_details.get("canBeEmpty")
    return can_be_empty is False


def apply_issue_field_shape_hint(
    field_info: dict[str, Any],
    issue_field_hint: Optional[dict[str, Any]],
) -> dict[str, Any]:
    if not issue_field_hint:
        return field_info

    merged = dict(field_info)
    hinted_issue_type = issue_field_hint.get("issue_field_type")
    if hinted_issue_type:
        merged["issue_field_type"] = hinted_issue_type
    hinted_bundle_type = issue_field_hint.get("bundle_element_type")
    if hinted_bundle_type:
        merged["bundle_element_type"] = hinted_bundle_type
    if issue_field_hint.get("is_multi_value"):
        merged["is_multi_value"] = True
    return merged


def extract_issue_custom_fields_map(issue_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for field in issue_data.get("customFields") or []:
        field_name = field.get("name")
        if field_name:
            result[field_name] = field
    return result


def extract_issue_field_shapes(issue_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    shapes: dict[str, dict[str, Any]] = {}
    for field_name, field in extract_issue_custom_fields_map(issue_data).items():
        value = field.get("value")
        bundle_element_type = None
        if isinstance(value, list):
            first_item = next((item for item in value if isinstance(item, dict)), None)
            if first_item:
                bundle_element_type = first_item.get("$type")
        elif isinstance(value, dict):
            bundle_element_type = value.get("$type")
        shapes[field_name] = {
            "issue_field_type": field.get("$type"),
            "bundle_element_type": bundle_element_type,
            "is_multi_value": isinstance(value, list),
        }
    return shapes


def extract_issue_field_cli_values(issue_field: Optional[dict[str, Any]]) -> list[str]:
    if not issue_field:
        return []

    value = issue_field.get("value")
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            item_value = first_non_empty(item.get("login"), item.get("name"), item.get("presentation"), item.get("text"))
            if item_value is not None:
                values.append(item_value)
        return values
    if isinstance(value, dict):
        item_value = first_non_empty(value.get("login"), value.get("name"), value.get("presentation"), value.get("text"))
        return [item_value] if item_value is not None else []
    if value is None:
        return []
    return [str(value)]


async def fetch_issue_field_snapshot(
    auth_manager: AuthManager,
    issue_id: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    issue_service = IssueService(auth_manager)
    try:
        result = await quiet_await(issue_service.get_issue(issue_id, fields=ISSUE_FIELD_SNAPSHOT_FIELDS))
    except Exception:
        return {}, {}
    if result["status"] != "success":
        return {}, {}
    issue_data = result["data"] or {}
    return extract_issue_field_shapes(issue_data), extract_issue_custom_fields_map(issue_data)


async def fetch_project_sample_issue_field_snapshot(
    auth_manager: AuthManager,
    project_ref: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], Optional[str]]:
    issue_service = IssueService(auth_manager)
    try:
        response = await issue_service._make_request(
            "GET",
            "issues",
            params={
                "query": f"project: {project_ref}",
                "$top": 1,
                "fields": ISSUE_FIELD_SNAPSHOT_FIELDS,
            },
        )
        result = await issue_service._handle_response(response)
    except Exception:
        return {}, {}, None
    if result["status"] != "success":
        return {}, {}, None
    issues = result["data"] or []
    if not issues:
        return {}, {}, None
    issue_data = issues[0] or {}
    return (
        extract_issue_field_shapes(issue_data),
        extract_issue_custom_fields_map(issue_data),
        first_non_empty(issue_data.get("idReadable"), issue_data.get("id")),
    )


async def resolve_existing_issue_field_context(
    auth_manager: AuthManager,
    *,
    parent_issue_id: Optional[str],
    project_lookup_ref: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], Optional[str]]:
    if parent_issue_id:
        shapes, fields = await fetch_issue_field_snapshot(auth_manager, parent_issue_id)
        if shapes:
            return shapes, fields, parent_issue_id
    return await fetch_project_sample_issue_field_snapshot(auth_manager, project_lookup_ref)


def normalized_issue_field_type(field_info: dict[str, Any]) -> str:
    issue_field_type = field_info.get("issue_field_type")
    if issue_field_type in {
        IssueCustomFieldTypes.SINGLE_ENUM,
        IssueCustomFieldTypes.MULTI_ENUM,
        IssueCustomFieldTypes.STATE,
        IssueCustomFieldTypes.SINGLE_USER,
        IssueCustomFieldTypes.MULTI_USER,
        IssueCustomFieldTypes.SINGLE_VERSION,
        IssueCustomFieldTypes.MULTI_VERSION,
        IssueCustomFieldTypes.TEXT,
        IssueCustomFieldTypes.PERIOD,
        IssueCustomFieldTypes.INTEGER,
    }:
        return issue_field_type

    project_field_type = field_info.get("project_field_type") or (field_info.get("field_details") or {}).get("$type") or ""
    if "MultiEnumProjectCustomField" in project_field_type:
        return IssueCustomFieldTypes.MULTI_ENUM
    if "EnumProjectCustomField" in project_field_type:
        return IssueCustomFieldTypes.SINGLE_ENUM
    if "StateProjectCustomField" in project_field_type:
        return IssueCustomFieldTypes.STATE
    if "MultiUserProjectCustomField" in project_field_type:
        return IssueCustomFieldTypes.MULTI_USER
    if "UserProjectCustomField" in project_field_type:
        return IssueCustomFieldTypes.SINGLE_USER
    if "MultiVersionProjectCustomField" in project_field_type:
        return IssueCustomFieldTypes.MULTI_VERSION
    if "VersionProjectCustomField" in project_field_type:
        return IssueCustomFieldTypes.SINGLE_VERSION
    if "TextProjectCustomField" in project_field_type:
        return IssueCustomFieldTypes.TEXT
    if "PeriodProjectCustomField" in project_field_type:
        return IssueCustomFieldTypes.PERIOD
    if "SimpleProjectCustomField" in project_field_type:
        return IssueCustomFieldTypes.INTEGER

    return IssueCustomFieldTypes.SINGLE_ENUM


def bundle_value_type(field_info: dict[str, Any]) -> Optional[str]:
    bundle_element_type = field_info.get("bundle_element_type")
    if bundle_element_type:
        return bundle_element_type

    issue_field_type = normalized_issue_field_type(field_info)
    if issue_field_type in {IssueCustomFieldTypes.SINGLE_ENUM, IssueCustomFieldTypes.MULTI_ENUM}:
        return CustomFieldValueTypes.ENUM_BUNDLE_ELEMENT
    if issue_field_type == IssueCustomFieldTypes.STATE:
        return CustomFieldValueTypes.STATE_BUNDLE_ELEMENT
    if issue_field_type in {IssueCustomFieldTypes.SINGLE_VERSION, IssueCustomFieldTypes.MULTI_VERSION}:
        return CustomFieldValueTypes.VERSION_BUNDLE_ELEMENT
    return None


def build_typed_field_payload(
    field_info: dict[str, Any],
    values: list[str],
) -> dict[str, Any]:
    issue_field_type = normalized_issue_field_type(field_info)
    field_name = field_info.get("field_name")
    if not field_name:
        fail(f"Missing field name in field info: {field_info}")

    if issue_field_type in SINGLE_VALUE_FIELD_TYPES and len(values) > 1:
        fail(f"Field '{field_name}' accepts a single value")

    if issue_field_type == IssueCustomFieldTypes.TEXT:
        return {
            "$type": IssueCustomFieldTypes.TEXT,
            "name": field_name,
            "value": {"$type": CustomFieldValueTypes.TEXT_VALUE, "text": values[0]},
        }

    if issue_field_type == IssueCustomFieldTypes.PERIOD:
        return {
            "$type": IssueCustomFieldTypes.PERIOD,
            "name": field_name,
            "value": {"$type": CustomFieldValueTypes.PERIOD_VALUE, "presentation": values[0]},
        }

    if issue_field_type == IssueCustomFieldTypes.INTEGER:
        try:
            numeric_value: Any = int(values[0]) if values[0].isdigit() else float(values[0])
        except (TypeError, ValueError):
            numeric_value = values[0]
        return {
            "$type": IssueCustomFieldTypes.INTEGER,
            "name": field_name,
            "value": numeric_value,
        }

    if issue_field_type == IssueCustomFieldTypes.SINGLE_USER:
        return {
            "$type": IssueCustomFieldTypes.SINGLE_USER,
            "name": field_name,
            "value": {"$type": CustomFieldValueTypes.USER, "login": values[0]},
        }

    if issue_field_type == IssueCustomFieldTypes.MULTI_USER:
        return {
            "$type": IssueCustomFieldTypes.MULTI_USER,
            "name": field_name,
            "value": [{"$type": CustomFieldValueTypes.USER, "login": value} for value in values],
        }

    bundle_type = bundle_value_type(field_info)
    if issue_field_type in {
        IssueCustomFieldTypes.SINGLE_ENUM,
        IssueCustomFieldTypes.STATE,
        IssueCustomFieldTypes.SINGLE_VERSION,
    }:
        return {
            "$type": issue_field_type,
            "name": field_name,
            "value": {"$type": bundle_type, "name": values[0]},
        }

    if issue_field_type in {
        IssueCustomFieldTypes.MULTI_ENUM,
        IssueCustomFieldTypes.MULTI_VERSION,
    }:
        return {
            "$type": issue_field_type,
            "name": field_name,
            "value": [{"$type": bundle_type, "name": value} for value in values],
        }

    fail(
        f"Unsupported custom field type for '{field_name}': "
        f"{issue_field_type or field_info.get('project_field_type') or 'unknown'}"
    )


def preview_field_value(field_payload: dict[str, Any]) -> Any:
    value = field_payload.get("value")
    if isinstance(value, list):
        return [item.get("login") or item.get("name") or item.get("presentation") for item in value]
    if isinstance(value, dict):
        return first_non_empty(value.get("login"), value.get("name"), value.get("presentation"), value.get("text"))
    return value


async def build_project_field_payloads(
    auth_manager: AuthManager,
    project_ref: str,
    field_assignments: dict[str, list[str]],
    existing_issue_field_shapes: Optional[dict[str, dict[str, Any]]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not field_assignments:
        return [], [], {}

    service = ProjectService(auth_manager)
    payloads: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []
    resolved_fields: dict[str, dict[str, Any]] = {}

    metadata_result = await quiet_await(
        service.get_project_custom_fields(project_ref, fields="id,canBeEmpty,field(name),$type")
    )
    if metadata_result["status"] != "success":
        fail(metadata_result["message"])
    project_field_metadata = {
        ((field.get("field") or {}).get("name") or "").lower(): field
        for field in metadata_result.get("data") or []
        if (field.get("field") or {}).get("name")
    }

    for field_name, values in field_assignments.items():
        result = await quiet_await(service.discover_custom_field(project_ref, field_name))
        if result["status"] != "success":
            fail(result["message"])
        field_info = dict(result["data"] or {})
        project_field = project_field_metadata.get(field_name.lower()) or {}
        if "canBeEmpty" in project_field:
            field_info["can_be_empty"] = project_field.get("canBeEmpty")
        if project_field.get("$type") and not field_info.get("project_field_type"):
            field_info["project_field_type"] = project_field.get("$type")
        field_info = apply_issue_field_shape_hint(field_info, (existing_issue_field_shapes or {}).get(field_name))
        payload = build_typed_field_payload(field_info, values)
        payloads.append(payload)
        resolved_fields[field_name] = field_info
        previews.append(
            {
                "name": payload["name"],
                "type": payload["$type"],
                "value": preview_field_value(payload),
                "required": field_preview_is_required(field_info),
            }
        )

    return payloads, previews, resolved_fields


def build_issue_mutation_preview(
    *,
    operation: str,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    project: Optional[dict[str, Any]] = None,
    parent_issue_id: Optional[str] = None,
    board: Optional[dict[str, Any]] = None,
    sprint_name: Optional[str] = None,
    field_previews: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    planned_actions = [{"type": "create_issue"}]
    if parent_issue_id:
        planned_actions.append({"type": "link_issue", "link_type": "Subtask", "target_issue_id": parent_issue_id})
    if board:
        planned_actions.append(
            {
                "type": "board_add",
                "board_id": board.get("id"),
                "board_name": board.get("name"),
                "sprint_name": sprint_name,
            }
        )

    return {
        "status": "success",
        "dry_run": dry_run,
        "operation": operation,
        "target": {
            "project": project,
            "board": (
                {
                    "id": board.get("id"),
                    "name": board.get("name"),
                }
                if board
                else None
            ),
            "parent_issue_id": parent_issue_id,
        },
        "issue": {
            "summary": summary,
            "description": description,
            "fields": field_previews,
        },
        "planned_actions": planned_actions,
        "warnings": [],
        "validation_errors": [],
    }


def normalize_server_error_message(error: Any) -> tuple[str, str]:
    server_error = error.get("message") if isinstance(error, dict) else str(error)
    message = server_error
    if message.startswith("Unexpected error: "):
        message = message[len("Unexpected error: ") :]
    if message.startswith("Request failed with status 400: "):
        message = message[len("Request failed with status 400: ") :]
    return message, server_error


async def build_issue_create_error_payload(
    auth_manager: AuthManager,
    prepared: dict[str, Any],
    error: Any,
) -> dict[str, Any]:
    message, server_error = normalize_server_error_message(error)
    operation = "create-subtask" if prepared.get("parent_issue_id") else "create-issue"
    project = prepared.get("project") or {}
    field_infos = prepared.get("field_infos") or {}
    existing_issue_field_shapes = prepared.get("existing_issue_field_shapes") or {}
    existing_issue_field_source = prepared.get("existing_issue_field_source")

    mismatch_match = re.search(r"Incompatible field type: (?P<field_id>\d+-\d+)", message)
    if mismatch_match:
        project_field_id = mismatch_match.group("field_id")
        field_name = None
        prepared_issue_field_type = None
        for name, field_info in field_infos.items():
            if field_info.get("field_id") == project_field_id:
                field_name = name
                prepared_issue_field_type = field_info.get("issue_field_type")
                break
        observed_issue_field_type = None
        if field_name:
            observed_issue_field_type = (existing_issue_field_shapes.get(field_name) or {}).get("issue_field_type")
        recovery_hint: dict[str, Any] = {
            "action": "inspect_field_type",
            "project_field_id": project_field_id,
            "note": "Do not retry with alternate CLI spellings, enum ids, or JSON-like string values.",
        }
        if field_name:
            recovery_hint["field_name"] = field_name
        if prepared_issue_field_type:
            recovery_hint["prepared_issue_field_type"] = prepared_issue_field_type
        if observed_issue_field_type:
            recovery_hint["observed_issue_field_type"] = observed_issue_field_type
        if existing_issue_field_source:
            recovery_hint["observed_from_issue_id"] = existing_issue_field_source
        return {
            "status": "error",
            "dry_run": False,
            "operation": operation,
            "error_kind": "field_type_mismatch",
            "message": message,
            "server_error": server_error,
            "recovery_hints": [recovery_hint],
        }

    required_match = re.search(r"(?P<field_name>.+?) is required$", message)
    if required_match:
        field_name = required_match.group("field_name")
        project_field_id = None
        project_lookup_ref = first_non_empty(project.get("shortName"), project.get("id"))
        if project_lookup_ref:
            project_service = ProjectService(auth_manager)
            field_result = await quiet_await(project_service.discover_custom_field(project_lookup_ref, field_name))
            if field_result["status"] == "success":
                project_field_id = (field_result.get("data") or {}).get("field_id")

        suggested_fields: list[str] = []
        parent_issue_id = prepared.get("parent_issue_id")
        if parent_issue_id:
            _, parent_issue_fields = await fetch_issue_field_snapshot(auth_manager, parent_issue_id)
            suggested_fields = [f"{field_name}={value}" for value in extract_issue_field_cli_values(parent_issue_fields.get(field_name))]

        recovery_hint: dict[str, Any] = {
            "action": "retry_with_fields",
            "field_name": field_name,
        }
        if project_field_id:
            recovery_hint["project_field_id"] = project_field_id
        if parent_issue_id:
            recovery_hint["parent_issue_id"] = parent_issue_id
        if suggested_fields:
            recovery_hint["fields"] = suggested_fields
        return {
            "status": "error",
            "dry_run": False,
            "operation": operation,
            "error_kind": "field_required",
            "message": message,
            "server_error": server_error,
            "recovery_hints": [recovery_hint],
        }

    return {
        "status": "error",
        "dry_run": False,
        "operation": operation,
        "error_kind": "server_error",
        "message": message,
        "server_error": server_error,
        "recovery_hints": [],
    }


async def execute_issue_command(
    auth_manager: AuthManager,
    manager: IssueManager,
    issue_id: str,
    query: str,
    dry_run: bool = False,
    comment: Optional[str] = None,
    raise_on_error: bool = True,
) -> dict[str, Any]:
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
        if raise_on_error:
            fail(result["message"])
        return {
            "status": "error",
            "issue_id": first_non_empty(issue.get("idReadable"), issue_id),
            "dry_run": dry_run,
            "query": query,
            "message": result["message"],
            "result": result.get("data"),
        }

    return {
        "status": "success",
        "issue_id": first_non_empty(issue.get("idReadable"), issue_id),
        "dry_run": dry_run,
        "query": query,
        "result": result["data"],
    }


async def handle_issue(
    args: argparse.Namespace,
    auth_manager: AuthManager,
    selection_label: str = "",
    base_url: Optional[str] = None,
) -> None:
    manager = IssueManager(auth_manager)
    board_service = AgileService(auth_manager)
    issue_service = IssueService(auth_manager)

    if args.issue_command == "brief":
        dump(await build_issue_brief_payload(issue_service, args.issue_id, base_url))
        return

    if args.issue_command in {"create", "create-subtask"}:
        prepared = await prepare_issue_create_operation(
            auth_manager,
            selection_label=selection_label,
            summary=args.summary,
            description=args.description,
            project_ref=args.project,
            board_ref=args.board,
            use_current_sprint=args.current_sprint,
            parent_issue_id=args.parent_issue_id if args.issue_command == "create-subtask" else None,
            type_name=args.type,
            priority=args.priority,
            assignee=args.assignee,
            mine=args.mine,
            me_from=args.me_from,
            raw_fields=args.field or [],
        )
        if not args.apply:
            dump(prepared["preview"])
            return
        dump(await apply_issue_create_operation(auth_manager, prepared, base_url=base_url))
        return

    if args.issue_command == "link":
        dump(
            await preview_or_apply_issue_link(
                auth_manager,
                source_issue_id=args.source_issue_id,
                target_issue_id=args.target_issue_id,
                link_type=args.link_type,
                apply=args.apply,
            )
        )
        return

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

    board_current = board_subparsers.add_parser(
        "current",
        help="Show the active board context for the selected instance or explicit board",
    )
    board_current.add_argument("--board", help="Board id or exact board name")

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

    board_tasks = board_subparsers.add_parser(
        "tasks",
        help="List tasks on the current sprint of a selected board",
    )
    board_tasks.add_argument("--board", help="Board id or exact board name")
    board_tasks.add_argument("--source", choices=["web", "strict"], default="web")
    board_tasks.add_argument("--assignee", help="Assignee full name/login or 'me'")
    board_tasks.add_argument("--mine", action="store_true")
    board_tasks.add_argument("--me-from", choices=["git-email-localpart"])
    board_tasks.add_argument("--initiator", help="Exact initiator full name/login")
    board_tasks.add_argument("--state", help="Exact state name")
    board_tasks.add_argument("--active-only", action="store_true")
    board_tasks.add_argument("--limit", type=int)

    board_my_tasks = board_subparsers.add_parser(
        "my-tasks",
        help="List current sprint tasks assigned to the current developer",
    )
    board_my_tasks.add_argument("--board", help="Board id or exact board name")
    board_my_tasks.add_argument("--source", choices=["web", "strict"], default="web")
    board_my_tasks.add_argument("--active-only", action="store_true")
    board_my_tasks.add_argument("--state", help="Exact state name")
    board_my_tasks.add_argument("--limit", type=int)

    board_create_task = board_subparsers.add_parser(
        "create-task",
        help="Create a task in a board/project context",
    )
    board_create_task.add_argument("--board", help="Board id or exact board name")
    board_create_task.add_argument("--project")
    board_create_task.add_argument("--summary", required=True)
    board_create_task.add_argument("--description")
    board_create_task.add_argument("--type")
    board_create_task.add_argument("--priority")
    board_create_task.add_argument("--assignee")
    board_create_task.add_argument("--mine", action="store_true")
    board_create_task.add_argument("--me-from", choices=["git-email-localpart"], default="git-email-localpart")
    board_create_task.add_argument("--field", action="append")
    board_create_task.add_argument("--current-sprint", action="store_true")
    board_create_task.add_argument("--apply", action="store_true")

    board_create_subtask = board_subparsers.add_parser(
        "create-subtask",
        help="Create a subtask in a board/project context",
    )
    board_create_subtask.add_argument("--board", help="Board id or exact board name")
    board_create_subtask.add_argument("--project")
    board_create_subtask.add_argument("--parent-issue-id", "--parent", dest="parent_issue_id", required=True)
    board_create_subtask.add_argument("--summary", required=True)
    board_create_subtask.add_argument("--description")
    board_create_subtask.add_argument("--type")
    board_create_subtask.add_argument("--priority")
    board_create_subtask.add_argument("--assignee")
    board_create_subtask.add_argument("--mine", action="store_true")
    board_create_subtask.add_argument("--me-from", choices=["git-email-localpart"], default="git-email-localpart")
    board_create_subtask.add_argument("--field", action="append")
    board_create_subtask.add_argument("--current-sprint", action="store_true")
    board_create_subtask.add_argument("--apply", action="store_true")

    issue = subparsers.add_parser("issue", help="Issue operations")
    issue_subparsers = issue.add_subparsers(dest="issue_command", required=True)

    issue_brief = issue_subparsers.add_parser("brief", help="Show a compact issue summary")
    issue_brief.add_argument("issue_id")

    issue_show = issue_subparsers.add_parser("show", help="Show issue details")
    issue_show.add_argument("issue_id")
    issue_show.add_argument("--raw", action="store_true")

    issue_create = issue_subparsers.add_parser(
        "create",
        help="Create an issue with preview-first semantics",
    )
    issue_create.add_argument("--project")
    issue_create.add_argument("--summary", required=True)
    issue_create.add_argument("--description")
    issue_create.add_argument("--type")
    issue_create.add_argument("--priority")
    issue_create.add_argument("--assignee")
    issue_create.add_argument("--mine", action="store_true")
    issue_create.add_argument("--me-from", choices=["git-email-localpart"], default="git-email-localpart")
    issue_create.add_argument("--field", action="append")
    issue_create.add_argument("--board", help="Board id or exact board name")
    issue_create.add_argument("--current-sprint", action="store_true")
    issue_create.add_argument("--apply", action="store_true")

    issue_create_subtask = issue_subparsers.add_parser(
        "create-subtask",
        help="Create a subtask with preview-first semantics",
    )
    issue_create_subtask.add_argument("--parent-issue-id", "--parent", dest="parent_issue_id", required=True)
    issue_create_subtask.add_argument("--project")
    issue_create_subtask.add_argument("--summary", required=True)
    issue_create_subtask.add_argument("--description")
    issue_create_subtask.add_argument("--type")
    issue_create_subtask.add_argument("--priority")
    issue_create_subtask.add_argument("--assignee")
    issue_create_subtask.add_argument("--mine", action="store_true")
    issue_create_subtask.add_argument("--me-from", choices=["git-email-localpart"], default="git-email-localpart")
    issue_create_subtask.add_argument("--field", action="append")
    issue_create_subtask.add_argument("--board", help="Board id or exact board name")
    issue_create_subtask.add_argument("--current-sprint", action="store_true")
    issue_create_subtask.add_argument("--apply", action="store_true")

    issue_link = issue_subparsers.add_parser(
        "link",
        help="Create an issue link with preview-first semantics",
    )
    issue_link.add_argument("--source", dest="source_issue_id", required=True)
    issue_link.add_argument("--target", dest="target_issue_id", required=True)
    issue_link.add_argument("--type", dest="link_type", required=True)
    issue_link.add_argument("--apply", action="store_true")

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
                await handle_issue(
                    args,
                    auth_manager,
                    selection.label,
                    base_url=base_url_for_label(selection.label),
                )
            return
        fail(f"Unknown command: {args.command}")
    except InstanceRuntimeError as exc:
        fail(str(exc))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

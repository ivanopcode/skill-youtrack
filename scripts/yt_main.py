#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import click

from instance_runtime import (
    InstanceRuntimeError,
    activated_keyring_service,
    clear_instance_scoped_board_ids,
    config_path_for_label,
    delete_instance_artifacts,
    detect_install_context,
    instance_known,
    instance_is_ready,
    instances_current_payload,
    instances_list_payload,
    register_instance,
    rename_instance,
    resolve_instance_selection,
    resolve_login_instance,
    set_instance_scoped_board_ids,
    set_active_instance,
    use_instance,
)
from youtrack_cli.main import main as upstream_main


@dataclass(frozen=True)
class WrapperArgs:
    instance: Optional[str]
    board_ids: list[str]
    no_auto_pin: bool
    forwarded: list[str]


def print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def build_instances_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yt instances", description="Manage named YouTrack instances")
    subparsers = parser.add_subparsers(dest="instances_command", required=True)

    subparsers.add_parser("list", help="List known YouTrack instances")
    subparsers.add_parser("current", help="Show the selected/current YouTrack instance")

    use_parser = subparsers.add_parser("use", help="Pin an instance as active for this install")
    use_parser.add_argument("label")

    rename_parser = subparsers.add_parser("rename", help="Rename a stored instance label")
    rename_parser.add_argument("source")
    rename_parser.add_argument("target")

    scope_parser = subparsers.add_parser("scope", help="Manage scoped agile board ids for an instance")
    scope_subparsers = scope_parser.add_subparsers(dest="scope_command", required=True)

    scope_set = scope_subparsers.add_parser("set", help="Replace the scoped board ids for an instance")
    scope_set.add_argument("label")
    scope_set.add_argument("board_ids", nargs="+", help="Board ids like 83-2561 or agiles/83-2561")

    scope_clear = scope_subparsers.add_parser("clear", help="Clear scoped board ids for an instance")
    scope_clear.add_argument("label")

    return parser


def parse_wrapper_args(argv: list[str]) -> WrapperArgs:
    instance: Optional[str] = None
    board_ids: list[str] = []
    no_auto_pin = False
    forwarded: list[str] = []

    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--instance":
            if index + 1 >= len(argv):
                fail("Missing value for --instance.")
            instance = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--instance="):
            instance = arg.split("=", 1)[1]
            index += 1
            continue
        if arg == "--board-id":
            if index + 1 >= len(argv):
                fail("Missing value for --board-id.")
            board_ids.append(argv[index + 1])
            index += 2
            continue
        if arg.startswith("--board-id="):
            board_ids.append(arg.split("=", 1)[1])
            index += 1
            continue
        if arg == "--no-auto-pin":
            no_auto_pin = True
            index += 1
            continue
        if arg in {"--config", "-c"} or arg.startswith("--config="):
            fail("Custom --config is not supported by this skill wrapper. Use --instance instead.")
        forwarded.append(arg)
        index += 1

    return WrapperArgs(instance=instance, board_ids=board_ids, no_auto_pin=no_auto_pin, forwarded=forwarded)


def is_help_only(args: list[str]) -> bool:
    if not args:
        return True
    return any(arg in {"-h", "--help", "--help-verbose", "--version"} for arg in args)


def is_auth_subcommand(args: list[str], subcommand: str) -> bool:
    return len(args) >= 2 and args[0] == "auth" and args[1] == subcommand


def run_upstream(args: list[str], *, selection_label: Optional[str] = None) -> None:
    command_args = list(args)
    if selection_label:
        command_args = ["--config", str(config_path_for_label(selection_label)), *command_args]

    try:
        if selection_label:
            with activated_keyring_service(selection_label):
                upstream_main.main(args=command_args, prog_name="yt", standalone_mode=False)
        else:
            upstream_main.main(args=command_args, prog_name="yt", standalone_mode=False)
    except click.exceptions.Exit as exc:
        raise SystemExit(exc.exit_code) from exc
    except click.ClickException as exc:
        click.echo(exc.format_message(), err=True)
        raise SystemExit(exc.exit_code) from exc
    except click.Abort as exc:
        click.echo("Aborted!", err=True)
        raise SystemExit(1) from exc


def handle_instances_command(skill_dir: Path, args: WrapperArgs) -> None:
    parser = build_instances_parser()
    parsed = parser.parse_args(args.forwarded[1:])

    if parsed.instances_command == "list":
        print_json(instances_list_payload(skill_dir, args.instance))
        return
    if parsed.instances_command == "current":
        print_json(instances_current_payload(skill_dir, args.instance))
        return
    if parsed.instances_command == "use":
        print_json(use_instance(skill_dir, parsed.label))
        return
    if parsed.instances_command == "rename":
        print_json(rename_instance(skill_dir, parsed.source, parsed.target))
        return
    if parsed.instances_command == "scope":
        if parsed.scope_command == "set":
            if not instance_known(parsed.label):
                fail(f"Unknown YouTrack instance '{parsed.label}'.")
            set_instance_scoped_board_ids(parsed.label, parsed.board_ids)
            print_json(instances_current_payload(skill_dir, parsed.label))
            return
        if parsed.scope_command == "clear":
            if not instance_known(parsed.label):
                fail(f"Unknown YouTrack instance '{parsed.label}'.")
            clear_instance_scoped_board_ids(parsed.label)
            print_json(instances_current_payload(skill_dir, parsed.label))
            return

    fail(f"Unknown instances command: {parsed.instances_command}")


def handle_auth_login(skill_dir: Path, args: WrapperArgs) -> None:
    selection = resolve_login_instance(skill_dir, args.instance)
    run_upstream(args.forwarded, selection_label=selection.label)
    register_instance(selection.label)
    if args.board_ids:
        set_instance_scoped_board_ids(selection.label, args.board_ids)
    if not args.no_auto_pin:
        set_active_instance(detect_install_context(skill_dir), selection.label)


def handle_auth_logout(skill_dir: Path, args: WrapperArgs) -> None:
    selection = resolve_instance_selection(skill_dir, args.instance)
    run_upstream(args.forwarded, selection_label=selection.label)
    if not instance_is_ready(selection.label):
        delete_instance_artifacts(selection.label)


def handle_forwarded_command(skill_dir: Path, args: WrapperArgs) -> None:
    if is_help_only(args.forwarded):
        if args.no_auto_pin:
            fail("--no-auto-pin is only valid with 'yt auth login'.")
        if args.board_ids:
            fail("--board-id is only valid with 'yt auth login'.")
        run_upstream(args.forwarded, selection_label=None)
        return

    if args.forwarded and args.forwarded[0] == "instances":
        if args.no_auto_pin:
            fail("--no-auto-pin is only valid with 'yt auth login'.")
        if args.board_ids:
            fail("--board-id is only valid with 'yt auth login'.")
        handle_instances_command(skill_dir, args)
        return

    if is_auth_subcommand(args.forwarded, "login"):
        handle_auth_login(skill_dir, args)
        return

    if args.no_auto_pin:
        fail("--no-auto-pin is only valid with 'yt auth login'.")
    if args.board_ids:
        fail("--board-id is only valid with 'yt auth login'.")

    if is_auth_subcommand(args.forwarded, "logout"):
        handle_auth_logout(skill_dir, args)
        return

    selection = resolve_instance_selection(skill_dir, args.instance)
    run_upstream(args.forwarded, selection_label=selection.label)


def main(argv: Optional[list[str]] = None) -> None:
    skill_dir = Path(__file__).resolve().parent.parent
    parsed = parse_wrapper_args(list(argv or sys.argv[1:]))

    if not parsed.forwarded:
        if parsed.no_auto_pin:
            fail("--no-auto-pin is only valid with 'yt auth login'.")
        if parsed.board_ids:
            fail("--board-id is only valid with 'yt auth login'.")
        run_upstream([], selection_label=None)
        return

    try:
        if parsed.forwarded[0] == "instances":
            if parsed.no_auto_pin:
                fail("--no-auto-pin is only valid with 'yt auth login'.")
            if parsed.board_ids:
                fail("--board-id is only valid with 'yt auth login'.")
            handle_instances_command(skill_dir, parsed)
            return
        handle_forwarded_command(skill_dir, parsed)
    except InstanceRuntimeError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()

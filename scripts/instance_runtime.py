#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import keyring
from dotenv import dotenv_values
from keyring.errors import PasswordDeleteError

from youtrack_cli.auth import AuthManager
from youtrack_cli.security import CredentialManager


SERVICE_PREFIX = "youtrack-cli"
INSTANCE_ENV_VAR = "YOUTRACK_INSTANCE"
LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
AUTH_ENV_KEYS = (
    "YOUTRACK_BASE_URL",
    "YOUTRACK_TOKEN",
    "YOUTRACK_USERNAME",
    "YOUTRACK_TOKEN_EXPIRY",
    "YOUTRACK_VERIFY_SSL",
    "YOUTRACK_CERT_FILE",
    "YOUTRACK_CA_BUNDLE",
    "YOUTRACK_API_KEY",
)
CREDENTIAL_KEYS = (
    "youtrack_base_url",
    "youtrack_token",
    "youtrack_username",
    "youtrack_token_expiry",
    "youtrack_verify_ssl",
    "youtrack_cert_file",
    "youtrack_ca_bundle",
)


class InstanceRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstallContext:
    skill_dir: Path
    install_root: Path
    install_kind: str
    install_id: str
    state_path: Path


@dataclass(frozen=True)
class InstanceSelection:
    label: str
    source: str
    config_path: Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def config_home() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg_config_home:
        return Path(xdg_config_home)
    return Path.home() / ".config"


def config_root() -> Path:
    return config_home() / SERVICE_PREFIX


def registry_path() -> Path:
    return config_root() / "registry.json"


def instances_dir() -> Path:
    return config_root() / "instances"


def installs_dir() -> Path:
    return config_root() / "installs"


def validate_label(label: str) -> str:
    normalized = label.strip()
    if not normalized:
        raise InstanceRuntimeError("Instance label cannot be empty.")
    if not LABEL_PATTERN.fullmatch(normalized):
        raise InstanceRuntimeError(
            "Invalid instance label. Use lowercase letters, digits, '.', '_' or '-'."
        )
    return normalized


def config_path_for_label(label: str) -> Path:
    return instances_dir() / f"{validate_label(label)}.env"


def keychain_service(label: str) -> str:
    return f"{SERVICE_PREFIX}:{validate_label(label)}"


def detect_install_context(skill_dir: Path) -> InstallContext:
    resolved_skill_dir = skill_dir.resolve()
    if resolved_skill_dir.parent.name == ".skills":
        install_root = resolved_skill_dir.parent.parent.resolve()
        install_kind = "local"
    else:
        install_root = resolved_skill_dir
        install_kind = "global"

    install_id = hashlib.sha256(str(install_root).encode("utf-8")).hexdigest()[:16]
    state_path = installs_dir() / f"{install_id}.json"
    return InstallContext(
        skill_dir=resolved_skill_dir,
        install_root=install_root,
        install_kind=install_kind,
        install_id=install_id,
        state_path=state_path,
    )


def login_hint(skill_dir: Path, label: str = "<label>") -> str:
    return (
        f"{skill_dir}/scripts/yt --instance {label} auth login "
        "--base-url https://your-youtrack-host"
    )


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstanceRuntimeError(f"Invalid JSON file: {path}") from exc
    if not isinstance(loaded, dict):
        raise InstanceRuntimeError(f"Expected JSON object in {path}")
    merged = dict(default)
    merged.update(loaded)
    return merged


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _normalize_labels(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        label = validate_label(value)
        if label not in normalized:
            normalized.append(label)
    return normalized


def _labels_from_instance_configs() -> list[str]:
    labels: list[str] = []
    if not instances_dir().exists():
        return labels
    for path in sorted(instances_dir().glob("*.env")):
        stem = path.stem
        if LABEL_PATTERN.fullmatch(stem) and stem not in labels:
            labels.append(stem)
    return labels


def load_registry() -> dict[str, Any]:
    registry = _load_json(registry_path(), {"instances": []})
    labels = _normalize_labels(registry.get("instances", []))
    changed = labels != registry.get("instances", [])

    for label in _labels_from_instance_configs():
        if label not in labels:
            labels.append(label)
            changed = True

    normalized = {"instances": labels}
    if changed or not registry_path().exists():
        _save_json(registry_path(), normalized)
    return normalized


def save_registry(labels: list[str]) -> dict[str, Any]:
    normalized = {"instances": _normalize_labels(labels)}
    _save_json(registry_path(), normalized)
    return normalized


def registered_instances() -> list[str]:
    return list(load_registry().get("instances", []))


def register_instance(label: str) -> list[str]:
    normalized_label = validate_label(label)
    labels = registered_instances()
    if normalized_label not in labels:
        labels.append(normalized_label)
        save_registry(labels)
    return labels


def unregister_instance(label: str) -> list[str]:
    normalized_label = validate_label(label)
    labels = [item for item in registered_instances() if item != normalized_label]
    save_registry(labels)
    return labels


def instance_config_values(label: str) -> dict[str, str]:
    path = config_path_for_label(label)
    if not path.exists():
        return {}
    try:
        values = dict(dotenv_values(path))
    except Exception as exc:
        raise InstanceRuntimeError(f"Invalid config file: {path}") from exc
    return {key: value for key, value in values.items() if value is not None}


def instance_known(label: str) -> bool:
    normalized_label = validate_label(label)
    return (
        normalized_label in registered_instances()
        or config_path_for_label(normalized_label).exists()
        or instance_has_any_keychain_data(normalized_label)
    )


def instance_has_any_keychain_data(label: str) -> bool:
    service = keychain_service(label)
    for key in (*CREDENTIAL_KEYS, CredentialManager.ENCRYPTION_KEY_NAME):
        try:
            if keyring.get_password(service, key) is not None:
                return True
        except Exception:
            continue
    return False


def instance_is_ready(label: str) -> bool:
    service = keychain_service(label)
    try:
        return bool(
            keyring.get_password(service, "youtrack_base_url")
            and keyring.get_password(service, "youtrack_token")
        )
    except Exception:
        return False


def load_install_state(context: InstallContext) -> dict[str, Any]:
    state = _load_json(
        context.state_path,
        {
            "install_id": context.install_id,
            "install_root": str(context.install_root),
            "install_kind": context.install_kind,
            "active_instance": None,
            "updated_at": None,
        },
    )
    active_instance = state.get("active_instance")
    if isinstance(active_instance, str) and active_instance.strip():
        try:
            state["active_instance"] = validate_label(active_instance)
        except InstanceRuntimeError:
            state["active_instance"] = None
    else:
        state["active_instance"] = None
    state["install_id"] = context.install_id
    state["install_root"] = str(context.install_root)
    state["install_kind"] = context.install_kind
    return state


def save_install_state(context: InstallContext, active_instance: Optional[str]) -> dict[str, Any]:
    payload = {
        "install_id": context.install_id,
        "install_root": str(context.install_root),
        "install_kind": context.install_kind,
        "active_instance": validate_label(active_instance) if active_instance else None,
        "updated_at": utc_now_iso(),
    }
    _save_json(context.state_path, payload)
    return payload


def get_active_instance(context: InstallContext) -> Optional[str]:
    return load_install_state(context).get("active_instance")


def set_active_instance(context: InstallContext, label: Optional[str]) -> dict[str, Any]:
    return save_install_state(context, label)


def _iter_install_state_paths() -> list[Path]:
    if not installs_dir().exists():
        return []
    return sorted(installs_dir().glob("*.json"))


def rewrite_active_instance_references(source: str, target: Optional[str]) -> None:
    source_label = validate_label(source)
    target_label = validate_label(target) if target else None
    for path in _iter_install_state_paths():
        try:
            payload = _load_json(
                path,
                {
                    "install_id": path.stem,
                    "install_root": "",
                    "install_kind": "global",
                    "active_instance": None,
                    "updated_at": None,
                },
            )
        except InstanceRuntimeError:
            continue
        active_instance = payload.get("active_instance")
        if active_instance != source_label:
            continue
        payload["active_instance"] = target_label
        payload["updated_at"] = utc_now_iso()
        _save_json(path, payload)


def _selection(label: str, source: str) -> InstanceSelection:
    normalized_label = validate_label(label)
    return InstanceSelection(
        label=normalized_label,
        source=source,
        config_path=config_path_for_label(normalized_label),
    )


def try_resolve_instance_selection(
    skill_dir: Path,
    cli_instance: Optional[str] = None,
) -> tuple[Optional[InstanceSelection], Optional[str]]:
    context = detect_install_context(skill_dir)
    labels = registered_instances()

    if cli_instance:
        label = validate_label(cli_instance)
        if instance_known(label):
            return _selection(label, "flag"), None
        return None, f"Unknown YouTrack instance '{label}'."

    env_instance = os.environ.get(INSTANCE_ENV_VAR, "").strip()
    if env_instance:
        label = validate_label(env_instance)
        if instance_known(label):
            return _selection(label, "env"), None
        return None, f"Unknown YouTrack instance from {INSTANCE_ENV_VAR}: '{label}'."

    active_instance = get_active_instance(context)
    if active_instance and instance_known(active_instance):
        return _selection(active_instance, "active"), None

    if len(labels) == 1:
        return _selection(labels[0], "sole"), None

    if not labels:
        return None, (
            "No YouTrack instances are configured. "
            f"Run {login_hint(context.skill_dir)}"
        )

    return None, (
        "Multiple YouTrack instances are configured. "
        "Use --instance <label> or 'yt instances use <label>'."
    )


def resolve_instance_selection(
    skill_dir: Path,
    cli_instance: Optional[str] = None,
    *,
    require_known: bool = True,
) -> InstanceSelection:
    selection, error = try_resolve_instance_selection(skill_dir, cli_instance)
    if selection is not None:
        return selection
    if not require_known and cli_instance:
        return _selection(cli_instance, "flag")
    raise InstanceRuntimeError(error or "Could not resolve YouTrack instance.")


def resolve_login_instance(skill_dir: Path, cli_instance: Optional[str]) -> InstanceSelection:
    if not cli_instance:
        raise InstanceRuntimeError(
            "auth login requires --instance <label>. "
            f"Example: {login_hint(skill_dir)}"
        )
    return resolve_instance_selection(skill_dir, cli_instance, require_known=False)


@contextlib.contextmanager
def activated_keyring_service(label: str) -> Iterator[None]:
    normalized_label = validate_label(label)
    saved_service = CredentialManager.KEYRING_SERVICE
    saved_env = {key: os.environ.get(key) for key in AUTH_ENV_KEYS}
    CredentialManager.KEYRING_SERVICE = keychain_service(normalized_label)
    for key in AUTH_ENV_KEYS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        CredentialManager.KEYRING_SERVICE = saved_service
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def activated_auth_manager(
    skill_dir: Path,
    cli_instance: Optional[str] = None,
    *,
    require_ready: bool = False,
) -> Iterator[tuple[InstallContext, InstanceSelection, AuthManager]]:
    context = detect_install_context(skill_dir)
    selection = resolve_instance_selection(skill_dir, cli_instance)
    with activated_keyring_service(selection.label):
        manager = AuthManager(config_path=str(selection.config_path))
        if require_ready and not manager.load_credentials():
            raise InstanceRuntimeError(
                f"Instance '{selection.label}' is not authenticated. "
                f"Run {login_hint(context.skill_dir, selection.label)}"
            )
        yield context, selection, manager


def instance_record(skill_dir: Path, label: str, selected_label: Optional[str] = None) -> dict[str, Any]:
    normalized_label = validate_label(label)
    context = detect_install_context(skill_dir)
    active_instance = get_active_instance(context)
    values = instance_config_values(normalized_label)
    return {
        "label": normalized_label,
        "config_path": str(config_path_for_label(normalized_label)),
        "base_url": values.get("YOUTRACK_BASE_URL"),
        "username": values.get("YOUTRACK_USERNAME"),
        "has_config": config_path_for_label(normalized_label).exists(),
        "ready": instance_is_ready(normalized_label),
        "active": active_instance == normalized_label,
        "selected": selected_label == normalized_label,
        "keychain_service": keychain_service(normalized_label),
    }


def instances_list_payload(skill_dir: Path, cli_instance: Optional[str] = None) -> dict[str, Any]:
    context = detect_install_context(skill_dir)
    selection, error = try_resolve_instance_selection(skill_dir, cli_instance)
    labels = registered_instances()
    selected_label = selection.label if selection else None
    return {
        "install": {
            "id": context.install_id,
            "kind": context.install_kind,
            "root": str(context.install_root),
            "active_instance": get_active_instance(context),
        },
        "selected_instance": {
            "label": selection.label,
            "source": selection.source,
        }
        if selection
        else None,
        "selection_error": error,
        "instances": [instance_record(skill_dir, label, selected_label) for label in labels],
    }


def instances_current_payload(skill_dir: Path, cli_instance: Optional[str] = None) -> dict[str, Any]:
    context = detect_install_context(skill_dir)
    selection, error = try_resolve_instance_selection(skill_dir, cli_instance)
    return {
        "install": {
            "id": context.install_id,
            "kind": context.install_kind,
            "root": str(context.install_root),
            "active_instance": get_active_instance(context),
        },
        "instance": instance_record(skill_dir, selection.label, selection.label) if selection else None,
        "source": selection.source if selection else None,
        "selection_error": error,
    }


def use_instance(skill_dir: Path, label: str) -> dict[str, Any]:
    normalized_label = validate_label(label)
    if not instance_known(normalized_label) or not instance_is_ready(normalized_label):
        raise InstanceRuntimeError(
            f"Instance '{normalized_label}' is not ready. "
            f"Run {login_hint(skill_dir, normalized_label)}"
        )
    register_instance(normalized_label)
    context = detect_install_context(skill_dir)
    set_active_instance(context, normalized_label)
    payload = instances_current_payload(skill_dir)
    payload["source"] = "active"
    return payload


def _collect_decrypted_credentials(label: str) -> dict[str, str]:
    credentials: dict[str, str] = {}
    with activated_keyring_service(label):
        manager = CredentialManager()
        for key in CREDENTIAL_KEYS:
            value = manager.retrieve_credential(key)
            if value:
                credentials[key] = value
    return credentials


def _store_decrypted_credentials(label: str, values: dict[str, str]) -> None:
    if not values:
        return
    with activated_keyring_service(label):
        manager = CredentialManager()
        for key, value in values.items():
            manager.store_credential(key, value)


def _delete_raw_keychain_entry(service: str, key: str) -> bool:
    try:
        keyring.delete_password(service, key)
        return True
    except PasswordDeleteError:
        return False
    except Exception:
        return False


def delete_instance_artifacts(label: str) -> dict[str, Any]:
    normalized_label = validate_label(label)
    config_path = config_path_for_label(normalized_label)
    config_removed = False
    if config_path.exists():
        config_path.unlink()
        config_removed = True

    removed_keys = {
        key: _delete_raw_keychain_entry(keychain_service(normalized_label), key)
        for key in (*CREDENTIAL_KEYS, CredentialManager.ENCRYPTION_KEY_NAME)
    }
    unregister_instance(normalized_label)
    rewrite_active_instance_references(normalized_label, None)
    return {
        "label": normalized_label,
        "config_removed": config_removed,
        "removed_keys": removed_keys,
    }


def rename_instance(skill_dir: Path, source: str, target: str) -> dict[str, Any]:
    source_label = validate_label(source)
    target_label = validate_label(target)
    if source_label == target_label:
        raise InstanceRuntimeError("Source and target instance labels must be different.")
    if not instance_known(source_label):
        raise InstanceRuntimeError(f"Unknown YouTrack instance '{source_label}'.")
    if instance_known(target_label):
        raise InstanceRuntimeError(f"Target YouTrack instance '{target_label}' already exists.")

    credentials = _collect_decrypted_credentials(source_label)
    source_config = config_path_for_label(source_label)
    target_config = config_path_for_label(target_label)

    if source_config.exists():
        target_config.parent.mkdir(parents=True, exist_ok=True)
        source_config.rename(target_config)

    _store_decrypted_credentials(target_label, credentials)
    for key in (*CREDENTIAL_KEYS, CredentialManager.ENCRYPTION_KEY_NAME):
        _delete_raw_keychain_entry(keychain_service(source_label), key)

    labels = [target_label if item == source_label else item for item in registered_instances()]
    if target_label not in labels:
        labels.append(target_label)
    save_registry(labels)
    rewrite_active_instance_references(source_label, target_label)

    payload = instances_current_payload(skill_dir)
    payload["renamed"] = {"from": source_label, "to": target_label}
    return payload

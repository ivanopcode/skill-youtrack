#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


MANIFEST_FILENAME = ".skill-install.json"
CATALOG_RELATIVE_PATH = Path("locales") / "metadata.json"
SUPPORTED_BASE_LOCALES = ("en", "ru")
SUPPORTED_LOCALE_MODES = ("en", "ru", "en-ru", "ru-en")
REQUIRED_LOCALE_KEYS = (
    "description",
    "display_name",
    "short_description",
    "default_prompt",
    "local_prefix",
)
SKILL_MD_DESCRIPTION_PATTERN = re.compile(r"^(description:\s*)(.*)$", flags=re.MULTILINE)
OPENAI_YAML_FIELD_TEMPLATE = r"^(\s*{key}:\s*)(.*)$"
CopyIgnore = shutil.ignore_patterns(
    ".git",
    ".venv",
    "__pycache__",
    ".DS_Store",
    "*.pyc",
    MANIFEST_FILENAME,
)


class SetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocaleSelection:
    mode: str
    primary_locale: str
    secondary_locale: Optional[str]


@dataclass(frozen=True)
class InstallResult:
    skill_name: str
    install_mode: str
    source_dir: Path
    runtime_dir: Path
    install_root: Path
    claude_link: Path
    codex_link: Path
    locale_mode: str


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def parse_locale_mode(value: str) -> LocaleSelection:
    normalized = value.strip().lower()
    if normalized not in SUPPORTED_LOCALE_MODES:
        supported = ", ".join(SUPPORTED_LOCALE_MODES)
        raise SetupError(f"Unsupported locale mode: {value}. Supported values: {supported}")

    if "-" in normalized:
        primary_locale, secondary_locale = normalized.split("-", 1)
        return LocaleSelection(
            mode=normalized,
            primary_locale=primary_locale,
            secondary_locale=secondary_locale,
        )

    return LocaleSelection(mode=normalized, primary_locale=normalized, secondary_locale=None)


def skill_data_home() -> Path:
    xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg_data_home:
        return Path(xdg_data_home)
    return Path.home() / ".local" / "share"


def managed_global_install_dir(skill_name: str) -> Path:
    return skill_data_home() / "agents" / "skills" / skill_name


def install_manifest_path(skill_dir: Path) -> Path:
    return skill_dir / MANIFEST_FILENAME


def load_install_manifest(skill_dir: Path) -> Optional[dict[str, Any]]:
    path = install_manifest_path(skill_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SetupError(f"Invalid install manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise SetupError(f"Expected JSON object in install manifest: {path}")
    return payload


def write_install_manifest(
    *,
    skill_dir: Path,
    skill_name: str,
    install_mode: str,
    locale_mode: str,
    source_dir: Path,
    runtime_dir: Path,
) -> None:
    selection = parse_locale_mode(locale_mode)
    payload = {
        "schema_version": 1,
        "skill_name": skill_name,
        "install_mode": install_mode,
        "locale_mode": selection.mode,
        "primary_locale": selection.primary_locale,
        "secondary_locale": selection.secondary_locale,
        "source_dir": str(source_dir),
        "runtime_dir": str(runtime_dir),
    }
    install_manifest_path(skill_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def resolve_source_dir(current_skill_dir: Path) -> Path:
    manifest = load_install_manifest(current_skill_dir)
    if manifest:
        candidate = manifest.get("source_dir")
        if isinstance(candidate, str) and candidate.strip():
            candidate_path = Path(candidate).expanduser()
            if candidate_path.exists():
                return candidate_path.resolve()
    return current_skill_dir.resolve()


def load_metadata_catalog(skill_dir: Path) -> dict[str, dict[str, str]]:
    path = skill_dir / CATALOG_RELATIVE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SetupError(f"Missing localization catalog: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SetupError(f"Invalid localization catalog: {path}") from exc

    locales = payload.get("locales")
    if not isinstance(locales, dict):
        raise SetupError(f"Localization catalog must contain a 'locales' object: {path}")

    normalized: dict[str, dict[str, str]] = {}
    for locale in SUPPORTED_BASE_LOCALES:
        locale_payload = locales.get(locale)
        if not isinstance(locale_payload, dict):
            raise SetupError(f"Missing locale '{locale}' in localization catalog: {path}")
        normalized_locale: dict[str, str] = {}
        for key in REQUIRED_LOCALE_KEYS:
            value = locale_payload.get(key)
            if not isinstance(value, str) or not value:
                raise SetupError(f"Locale '{locale}' is missing string field '{key}' in {path}")
            normalized_locale[key] = value
        normalized[locale] = normalized_locale
    return normalized


def build_localized_metadata(skill_dir: Path, locale_mode: str, install_mode: str) -> dict[str, str]:
    selection = parse_locale_mode(locale_mode)
    catalog = load_metadata_catalog(skill_dir)
    primary = catalog[selection.primary_locale]

    description = primary["description"]
    if selection.secondary_locale is not None:
        secondary = catalog[selection.secondary_locale]
        description = f"{description} / {secondary['description']}"

    display_name = primary["display_name"]
    short_description = primary["short_description"]
    default_prompt = primary["default_prompt"]

    if install_mode == "local":
        prefix = primary["local_prefix"]
        description = f"{prefix}{description}"
        display_name = f"{prefix}{display_name}"
        short_description = f"{prefix}{short_description}"

    return {
        "description": description,
        "display_name": display_name,
        "short_description": short_description,
        "default_prompt": default_prompt,
    }


def render_skill_metadata(skill_dir: Path, locale_mode: str, install_mode: str) -> None:
    metadata = build_localized_metadata(skill_dir, locale_mode, install_mode)

    skill_md_path = skill_dir / "SKILL.md"
    skill_text = skill_md_path.read_text(encoding="utf-8")
    skill_text, description_count = SKILL_MD_DESCRIPTION_PATTERN.subn(
        lambda match: f"{match.group(1)}{yaml_quote(metadata['description'])}",
        skill_text,
        count=1,
    )
    if description_count != 1:
        raise SetupError(f"Could not update SKILL.md description in {skill_md_path}")
    skill_md_path.write_text(skill_text, encoding="utf-8")

    openai_yaml_path = skill_dir / "agents" / "openai.yaml"
    yaml_text = openai_yaml_path.read_text(encoding="utf-8")
    for key in ("display_name", "short_description", "default_prompt"):
        pattern = re.compile(OPENAI_YAML_FIELD_TEMPLATE.format(key=key), flags=re.MULTILINE)
        yaml_text, count = pattern.subn(
            lambda match, value=metadata[key]: f"{match.group(1)}{yaml_quote(value)}",
            yaml_text,
            count=1,
        )
        if count != 1:
            raise SetupError(f"Could not update {key} in {openai_yaml_path}")
    openai_yaml_path.write_text(yaml_text, encoding="utf-8")


def sync_skill_copy(source_dir: Path, dest_dir: Path) -> None:
    if dest_dir.is_symlink() or dest_dir.is_file():
        dest_dir.unlink()
    elif dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=False)
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, dest_dir, ignore=CopyIgnore)


def ensure_skill_link(link_value: str, target_path: Path) -> None:
    if target_path.is_symlink() or target_path.is_file():
        target_path.unlink()
    elif target_path.exists():
        raise SetupError(f"Refusing to replace existing directory: {target_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(link_value, target_path)


def run_bootstrap(skill_dir: Path) -> None:
    subprocess.run([str(skill_dir / "scripts" / "bootstrap.sh"), "--quiet"], check=True)


def resolve_repo_root(path: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SetupError(f"Local mode expects a git repository: {path}")
    return Path(completed.stdout.strip()).resolve()


def resolve_locale_mode(install_mode: str, runtime_dir: Path, requested_locale: Optional[str]) -> str:
    manifest = load_install_manifest(runtime_dir)
    existing_locale = manifest.get("locale_mode") if manifest else None
    if existing_locale is not None and not isinstance(existing_locale, str):
        raise SetupError(f"Invalid locale_mode in install manifest: {install_manifest_path(runtime_dir)}")

    if requested_locale:
        requested_mode = parse_locale_mode(requested_locale).mode
        if install_mode == "local" and existing_locale and existing_locale != requested_mode:
            raise SetupError(
                "Local install locale is project-fixed after the first install. "
                f"Expected {existing_locale}, got {requested_mode}."
            )
        return requested_mode

    if existing_locale:
        return parse_locale_mode(existing_locale).mode

    supported = ", ".join(SUPPORTED_LOCALE_MODES)
    raise SetupError(
        f"First {install_mode} install requires --locale <{supported}>."
    )


def perform_install(
    *,
    source_dir: Path,
    install_mode: str,
    requested_locale: Optional[str],
    repo_root: Optional[Path] = None,
    bootstrap_runner: Callable[[Path], None] = run_bootstrap,
) -> InstallResult:
    source_dir = resolve_source_dir(source_dir).resolve()
    skill_name = source_dir.name

    if install_mode == "global":
        install_root = Path.home()
        runtime_dir = managed_global_install_dir(skill_name)
        claude_link_value = str(runtime_dir)
        codex_link_value = str(runtime_dir)
    elif install_mode == "local":
        if repo_root is None:
            raise SetupError("Local install requires a repository path.")
        install_root = resolve_repo_root(repo_root)
        runtime_dir = install_root / ".skills" / skill_name
        claude_link_value = f"../../.skills/{skill_name}"
        codex_link_value = f"../../.skills/{skill_name}"
    else:
        raise SetupError(f"Unsupported install mode: {install_mode}")

    locale_mode = resolve_locale_mode(install_mode, runtime_dir, requested_locale)

    sync_skill_copy(source_dir, runtime_dir)
    render_skill_metadata(runtime_dir, locale_mode, install_mode)
    write_install_manifest(
        skill_dir=runtime_dir,
        skill_name=skill_name,
        install_mode=install_mode,
        locale_mode=locale_mode,
        source_dir=source_dir,
        runtime_dir=runtime_dir,
    )
    bootstrap_runner(runtime_dir)

    claude_link = install_root / ".claude" / "skills" / skill_name
    codex_link = install_root / ".codex" / "skills" / skill_name
    ensure_skill_link(claude_link_value, claude_link)
    ensure_skill_link(codex_link_value, codex_link)

    return InstallResult(
        skill_name=skill_name,
        install_mode=install_mode,
        source_dir=source_dir,
        runtime_dir=runtime_dir,
        install_root=install_root,
        claude_link=claude_link,
        codex_link=codex_link,
        locale_mode=locale_mode,
    )

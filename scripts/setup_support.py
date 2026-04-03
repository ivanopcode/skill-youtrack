#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


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
    "triggers",
)
OPENAI_YAML_FIELD_TEMPLATE = r"^(\s*{key}:\s*)(.*)$"
FRONTMATTER_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_-]+):(.*)$")
CopyIgnore = shutil.ignore_patterns(
    ".git",
    ".venv",
    "__pycache__",
    ".DS_Store",
    "*.pyc",
    MANIFEST_FILENAME,
)
LOCAL_RUNTIME_PRUNE_PATHS = (
    Path("README.md"),
    CATALOG_RELATIVE_PATH,
    Path("scripts") / "setup_main.py",
    Path("scripts") / "setup_support.py",
    Path("tests"),
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
    locale_mode: str


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(cleaned)
    return results


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
    source_dir: Optional[Path],
) -> None:
    selection = parse_locale_mode(locale_mode)
    payload = {
        "schema_version": 2,
        "skill_name": skill_name,
        "install_mode": install_mode,
        "locale_mode": selection.mode,
        "primary_locale": selection.primary_locale,
        "secondary_locale": selection.secondary_locale,
    }
    if source_dir is not None:
        payload["source_dir"] = str(source_dir)
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


def load_metadata_catalog(skill_dir: Path) -> dict[str, dict[str, Any]]:
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

    normalized: dict[str, dict[str, Any]] = {}
    for locale in SUPPORTED_BASE_LOCALES:
        locale_payload = locales.get(locale)
        if not isinstance(locale_payload, dict):
            raise SetupError(f"Missing locale '{locale}' in localization catalog: {path}")
        normalized_locale: dict[str, Any] = {}
        for key in REQUIRED_LOCALE_KEYS:
            value = locale_payload.get(key)
            if key == "triggers":
                if not isinstance(value, list) or not value:
                    raise SetupError(f"Locale '{locale}' must define a non-empty trigger list in {path}")
                triggers: list[str] = []
                for item in value:
                    if not isinstance(item, str) or not item.strip():
                        raise SetupError(f"Locale '{locale}' contains an invalid trigger in {path}")
                    triggers.append(item)
                normalized_locale[key] = triggers
                continue
            if not isinstance(value, str) or not value:
                raise SetupError(f"Locale '{locale}' is missing string field '{key}' in {path}")
            normalized_locale[key] = value
        normalized[locale] = normalized_locale
    return normalized


def build_localized_metadata(skill_dir: Path, locale_mode: str, install_mode: str) -> dict[str, Any]:
    selection = parse_locale_mode(locale_mode)
    catalog = load_metadata_catalog(skill_dir)
    primary = catalog[selection.primary_locale]

    description = primary["description"]
    triggers = list(primary["triggers"])
    if selection.secondary_locale is not None:
        secondary = catalog[selection.secondary_locale]
        description = f"{description} / {secondary['description']}"
        triggers = unique_strings([*triggers, *secondary["triggers"]])

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
        "triggers": triggers,
    }


def parse_frontmatter_sections(skill_text: str) -> tuple[list[tuple[str, str]], str]:
    lines = skill_text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise SetupError("SKILL.md must start with a YAML frontmatter block.")

    closing_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break
    if closing_index is None:
        raise SetupError("SKILL.md frontmatter is missing a closing --- line.")

    frontmatter_lines = lines[1:closing_index]
    body = "".join(lines[closing_index + 1 :])
    sections: list[tuple[str, str]] = []
    current_key: str | None = None
    current_lines: list[str] = []

    for line in frontmatter_lines:
        match = FRONTMATTER_KEY_RE.match(line)
        if match:
            if current_key is not None:
                sections.append((current_key, "".join(current_lines)))
            current_key = match.group("key")
            current_lines = [line]
            continue
        if current_key is None:
            raise SetupError("SKILL.md frontmatter contains content before the first key.")
        current_lines.append(line)

    if current_key is not None:
        sections.append((current_key, "".join(current_lines)))

    return sections, body


def render_triggers_block(triggers: list[str]) -> str:
    lines = ["triggers:\n"]
    for trigger in triggers:
        lines.append(f"  - {yaml_quote(trigger)}\n")
    return "".join(lines)


def replace_frontmatter_sections(skill_text: str, replacements: dict[str, str]) -> str:
    sections, body = parse_frontmatter_sections(skill_text)
    index_by_key = {key: index for index, (key, _) in enumerate(sections)}

    for key, rendered_value in replacements.items():
        if key in index_by_key:
            sections[index_by_key[key]] = (key, rendered_value)
            continue

        insert_at = len(sections)
        if key == "triggers" and "description" in index_by_key:
            insert_at = index_by_key["description"] + 1
        sections.insert(insert_at, (key, rendered_value))
        index_by_key = {section_key: index for index, (section_key, _) in enumerate(sections)}

    frontmatter = "".join(section for _, section in sections)
    return f"---\n{frontmatter}---\n{body}"


def render_skill_metadata(skill_dir: Path, locale_mode: str, install_mode: str) -> None:
    metadata = build_localized_metadata(skill_dir, locale_mode, install_mode)

    skill_md_path = skill_dir / "SKILL.md"
    skill_text = skill_md_path.read_text(encoding="utf-8")
    skill_text = replace_frontmatter_sections(
        skill_text,
        {
            "description": f"description: {yaml_quote(metadata['description'])}\n",
            "triggers": render_triggers_block(metadata["triggers"]),
        },
    )
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


def prune_local_runtime_copy(skill_dir: Path) -> None:
    for relative_path in LOCAL_RUNTIME_PRUNE_PATHS:
        target_path = skill_dir / relative_path
        if target_path.is_symlink() or target_path.is_file():
            target_path.unlink()
            continue
        if target_path.is_dir():
            shutil.rmtree(target_path, ignore_errors=False)

    locales_dir = skill_dir / "locales"
    if locales_dir.is_dir() and not any(locales_dir.iterdir()):
        locales_dir.rmdir()


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
    raise SetupError(f"First {install_mode} install requires --locale <{supported}>.")


def perform_install(
    *,
    source_dir: Path,
    install_mode: str,
    requested_locale: Optional[str],
    repo_root: Optional[Path] = None,
) -> InstallResult:
    source_dir = resolve_source_dir(source_dir).resolve()
    skill_name = source_dir.name

    if install_mode != "local":
        raise SetupError(f"Unsupported install mode: {install_mode}. Only local is supported.")
    if repo_root is None:
        raise SetupError("Local install requires a repository path.")

    install_root = resolve_repo_root(repo_root)
    runtime_dir = install_root / ".agents" / "skills" / skill_name

    locale_mode = resolve_locale_mode(install_mode, runtime_dir, requested_locale)

    sync_skill_copy(source_dir, runtime_dir)
    render_skill_metadata(runtime_dir, locale_mode, install_mode)
    prune_local_runtime_copy(runtime_dir)
    write_install_manifest(
        skill_dir=runtime_dir,
        skill_name=skill_name,
        install_mode=install_mode,
        locale_mode=locale_mode,
        source_dir=None,
    )
    return InstallResult(
        skill_name=skill_name,
        install_mode=install_mode,
        source_dir=source_dir,
        runtime_dir=runtime_dir,
        install_root=install_root,
        locale_mode=locale_mode,
    )

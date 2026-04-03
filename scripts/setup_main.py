#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from setup_support import (
    SUPPORTED_LOCALE_MODES,
    SetupError,
    perform_install,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="make install",
        description="Copy skill-youtrack into one repository-local agent runtime.",
    )
    locale_help = (
        "Locale mode for installed metadata. "
        f"Supported: {', '.join(SUPPORTED_LOCALE_MODES)}. "
        "Required on first install, optional on reruns when an install manifest already exists."
    )
    parser.add_argument("repo_path", help="Path to the git repository root or any path inside that repository")
    parser.add_argument("--locale", help=locale_help)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    current_skill_dir = Path(__file__).resolve().parent.parent

    try:
        result = perform_install(
            source_dir=current_skill_dir,
            install_mode="local",
            requested_locale=args.locale,
            repo_root=Path(args.repo_path).expanduser(),
        )
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Installed {result.skill_name}")
    print(f"  Source: {result.source_dir}")
    print(f"  Locale: {result.locale_mode}")
    print(f"  Project copy: {result.runtime_dir}")
    print()
    print("Next step:")
    print(f"  make -C {result.runtime_dir} skill")


if __name__ == "__main__":
    main()

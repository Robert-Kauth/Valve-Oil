#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.runtime.board_images import validate_board_targets
from installer.runtime.compatibility import load_supported_upgrade_sources, validate_manifest_compatibility
from installer.runtime.manifest import load_manifest
from scripts.build_installer_bundle import ALLOWED_DIRECTORIES, ALLOWED_FILES


# What the release bundle is built from: a change to any of it ships under a new package version.
BUNDLE_SOURCES = [*ALLOWED_FILES, *ALLOWED_DIRECTORIES, "ui"]
VERSION_HEADING = re.compile(r"^## (\S+)\s*$", re.MULTILINE)

README_OFFICIAL = "## Supported printers"
README_COMMUNITY = "### Community-supported printers"


def _readme_table(text: str, start: str, end: str) -> dict[str, str]:
    """A README section's printer table, printer to firmware column; an empty dict for "None yet."."""
    section = text.split(start, 1)[1].split(end, 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ")][1:]  # without the header row
    return {cells[0]: cells[1] for cells in ([cell.strip() for cell in row.strip("|").split("|")] for row in rows)}


def check_readme_printers(manifest, readme: str) -> None:
    """The README lists each printer in its support level's table, with the QIDI firmware it supports."""
    for heading in (README_OFFICIAL, README_COMMUNITY):
        if heading not in readme:
            raise SystemExit(f"README.md is missing its '{heading}' section")
    tables = {
        "official": _readme_table(readme, README_OFFICIAL, README_COMMUNITY),
        "community": _readme_table(readme, README_COMMUNITY, "\n## "),
    }
    for support, listed in tables.items():
        expected = {printer.display_name: ", ".join(f"`{printer.soc_version(key)}`" for key in printer.firmware)
                    for printer in manifest.printers if printer.support == support}
        if listed != expected:
            raise SystemExit(f"README.md's {support} printers {listed} don't match installer/package.yaml: {expected}")


def check_readme_links(readme: str) -> None:
    """The README is the operators' landing page; the repository's specs aren't for them."""
    if re.search(r"\]\((?:\.?/)?openspec/", readme):
        raise SystemExit("README.md links openspec/; link the operator docs in docs/ instead")


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def check_changelog(version: str, changelog: str) -> None:
    """CHANGELOG.md has a section for the package version on top, then each earlier version once, newest first."""
    headings = VERSION_HEADING.findall(changelog)
    if not headings or headings[0] != version:
        raise SystemExit(f"CHANGELOG.md's first section is {headings[:1]}; add a '## {version}' section above it "
                         "for what this version changes for operators")
    try:
        keys = [_version_key(heading) for heading in headings]
    except ValueError:
        raise SystemExit(f"CHANGELOG.md has a section heading that isn't a version: {headings}") from None
    if any(newer <= older for newer, older in zip(keys, keys[1:])):
        raise SystemExit("CHANGELOG.md's version sections aren't each listed once, newest first")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout


def check_version_advanced(version: str) -> None:
    """A change to what the release bundle is built from ships under a version newer than the last release."""
    tags = [tag[1:] for tag in _git("tag", "--list", "v*").split() if re.fullmatch(r"v\d+(?:\.\d+)+", tag)]
    if not tags:
        raise SystemExit("No release tags (v*) to compare the package version with; fetch them with "
                         "`git fetch --tags`")
    released = max(tags, key=_version_key)
    if _version_key(version) > _version_key(released):
        return
    if _version_key(version) < _version_key(released):
        raise SystemExit(f"Package version {version} is older than the last release, {released}")
    changed = _git("diff", "--name-only", f"v{released}", "--", *BUNDLE_SOURCES).split()
    changed += _git("ls-files", "--others", "--exclude-standard", "--", *BUNDLE_SOURCES).split()
    if changed:
        raise SystemExit(f"{len(changed)} release-bundle files changed since v{released} (first: {changed[0]}); "
                         "advance the version with `python3 scripts/bump_installer_version.py <version>`")


def main() -> int:
    manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    check_readme_printers(manifest, readme)
    check_readme_links(readme)
    check_changelog(manifest.package.version, (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    check_version_advanced(manifest.package.version)
    compatibility = load_supported_upgrade_sources(
        REPO_ROOT / "installer/supported_upgrade_sources.yaml"
    )
    validate_manifest_compatibility(manifest, compatibility)
    validate_board_targets(manifest, REPO_ROOT)
    for printer in manifest.printers:
        # A firmware update sends the closed-loop driver image only when its size and SHA-256 are pinned.
        target = printer.target_firmware
        if (target.board_version("closed_loop") is None) != (target.closed_loop_image is None):
            raise SystemExit(f"{printer.id}: target firmware {target.version} closed_loop_image doesn't match its "
                             "closed-loop drivers")
    globals_text = (REPO_ROOT / "installer/klipper/macros/globals.cfg").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^variable_package_version:\s*["\']?([^"\'\n]+)["\']?\s*$', globals_text, re.MULTILINE)
    if match is None:
        raise SystemExit("Missing variable_package_version in Valve Oil's globals.cfg")
    if match.group(1) != manifest.package.version:
        raise SystemExit(
            "Optimized globals package version does not match installer/package.yaml: "
            f"{match.group(1)} != {manifest.package.version}"
        )
    print("installer compatibility metadata validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

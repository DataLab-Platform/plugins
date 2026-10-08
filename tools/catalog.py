# Copyright (c) DataLab Platform Developers, BSD 3-Clause license, see LICENSE file.

"""Check DataLab plugin catalog entries and build the published catalog.

Usage::

    python tools/catalog.py check             # validate entries (pull requests)
    python tools/catalog.py build -o site     # catalog.json, mirrored wheels, index

Dependencies of plugin wheels are checked against the distributions installed
in the running environment: install the latest DataLab release first.
"""

from __future__ import annotations

import argparse
import configparser
import datetime
import hashlib
import html
import io
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Mapping
from email.message import Message
from email.parser import BytesParser
from email.policy import default as email_policy
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wheels  # noqa: E402  (verbatim copy of datalab/plugins/wheels.py)

ROOT = Path(__file__).resolve().parents[1]
ENTRIES_DIR = ROOT / "plugins"
SCHEMA_PATH = ROOT / "schema" / "plugin-entry.schema.json"
CATALOG_SCHEMA_VERSION = 1
OFFICIAL_OWNER = "datalab-platform"
OFFICIAL_ID_PREFIX = "org.datalab."
TARGET_GROUPS = (
    ("desktop", wheels.DESKTOP_ENTRY_POINT_GROUP),
    ("web", wheels.WEB_ENTRY_POINT_GROUP),
)
#: OSI-approved licenses accepted in the catalog (SPDX identifiers)
ALLOWED_LICENSES = frozenset(
    {
        "0BSD",
        "AFL-3.0",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "BSL-1.0",
        "CECILL-2.1",
        "EPL-2.0",
        "EUPL-1.2",
        "GPL-2.0-only",
        "GPL-2.0-or-later",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "ISC",
        "LGPL-2.1-only",
        "LGPL-2.1-or-later",
        "LGPL-3.0-only",
        "LGPL-3.0-or-later",
        "MIT",
        "MPL-2.0",
        "PSF-2.0",
        "Unlicense",
        "Zlib",
    }
)
_LICENSE_OPERATORS = frozenset({"AND", "OR"})


class CatalogError(Exception):
    """Raised when catalog entries cannot be accepted."""


def fetch(url: str) -> bytes:
    """Return the content of an HTTPS resource."""
    if not url.startswith("https://"):
        raise CatalogError(f"Refusing a non-HTTPS URL: {url}")
    headers = {"User-Agent": "datalab-plugin-catalog"}
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read(wheels.MAX_WHEEL_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CatalogError(f"Cannot download {url}: {exc}") from exc


def get_host_distributions() -> dict[str, str]:
    """Return the distributions installed in the running environment."""
    distributions: dict[str, str] = {}
    for distribution in importlib_metadata.distributions():
        name = distribution.metadata["Name"]
        if name:
            distributions.setdefault(name, distribution.version)
    return distributions


def load_entry(path: Path, validator: jsonschema.Draft202012Validator) -> dict:
    """Load and validate a catalog entry file."""
    # BaseLoader keeps every scalar as a string: "1.10" must not become 1.1
    entry = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    errors = sorted(validator.iter_errors(entry), key=lambda error: error.path)
    if errors:
        details = "; ".join(
            f"{'/'.join(str(part) for part in error.path) or 'entry'}: {error.message}"
            for error in errors
        )
        raise CatalogError(f"{path.name}: {details}")
    if path.stem != entry["id"]:
        raise CatalogError(f"{path.name}: the file must be named {entry['id']}.yaml")
    return entry


def resolve_wheel(entry: dict, release: dict) -> tuple[str, str]:
    """Return the file name and download URL of a release wheel."""
    if release.get("source", "github") == "pypi":
        distribution = urllib.parse.quote(release["distribution"])
        version = urllib.parse.quote(release["version"])
        info = json.loads(fetch(f"https://pypi.org/pypi/{distribution}/{version}/json"))
        files = [
            (item["filename"], item["url"])
            for item in info["urls"]
            if item["packagetype"] == "bdist_wheel"
        ]
        where = f"PyPI release {release['distribution']} {release['version']}"
    else:
        owner_repo = entry["repository"].removeprefix("https://github.com/")
        tag = release.get("tag", f"v{release['version']}")
        info = json.loads(
            fetch(
                f"https://api.github.com/repos/{owner_repo}/releases/tags/"
                + urllib.parse.quote(tag, safe="")
            )
        )
        files = [
            (asset["name"], asset["browser_download_url"]) for asset in info["assets"]
        ]
        where = f"GitHub release {tag} of {entry['repository']}"
    pure_wheels = [item for item in files if item[0].endswith("-py3-none-any.whl")]
    if len(pure_wheels) != 1:
        raise CatalogError(
            f"{where}: expected one pure-Python wheel (*-py3-none-any.whl), "
            f"found {len(pure_wheels)}"
        )
    return pure_wheels[0]


def _dist_info_file(archive: zipfile.ZipFile, name: str) -> bytes | None:
    paths = [
        path
        for path in archive.namelist()
        if path.count("/") == 1 and path.endswith(f".dist-info/{name}")
    ]
    return archive.read(paths[0]) if len(paths) == 1 else None


def _read_wheel_metadata(data: bytes) -> tuple[set[str], Message]:
    """Return the entry-point groups and the core metadata of a wheel."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entry_points = _dist_info_file(archive, "entry_points.txt")
            metadata = _dist_info_file(archive, "METADATA") or b""
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string((entry_points or b"").decode("utf-8"))
        groups = set(parser.sections())
    except (zipfile.BadZipFile, configparser.Error, UnicodeDecodeError):
        # Let the wheel inspection report what is wrong with the archive
        groups = {wheels.DESKTOP_ENTRY_POINT_GROUP}
        metadata = b""
    return groups, BytesParser(policy=email_policy).parsebytes(metadata)


def _check_license(metadata: Message, label: str) -> str:
    """Return the license of an accepted wheel."""
    expression = metadata.get("License-Expression")
    if expression:
        tokens = expression.replace("(", " ").replace(")", " ").split()
        identifiers = [
            token
            for index, token in enumerate(tokens)
            if token not in _LICENSE_OPERATORS
            and token != "WITH"
            and (index == 0 or tokens[index - 1] != "WITH")
        ]
        refused = [name for name in identifiers if name not in ALLOWED_LICENSES]
        if refused:
            raise CatalogError(
                f"{label}: license {', '.join(refused)} is not an accepted "
                "OSI-approved license (see CONTRIBUTING.md)"
            )
        return expression
    if any(
        classifier.startswith("License :: OSI Approved")
        for classifier in metadata.get_all("Classifier", [])
    ):
        return metadata.get("License") or "OSI Approved"
    raise CatalogError(f"{label}: the wheel declares no License-Expression metadata")


def check_release(
    entry: dict, release: dict, available: Mapping[str, str]
) -> tuple[dict, dict, bytes]:
    """Download, verify and inspect one release.

    Returns:
        Catalog release record, package information and wheel content
    """
    label = f"{entry['id']} {release['version']}"
    filename, url = resolve_wheel(entry, release)
    data = fetch(url)
    digest = hashlib.sha256(data).hexdigest()
    if release.get("sha256") != digest:
        raise CatalogError(
            f"{label}: the wheel digest must be declared, add to this release: "
            f"sha256: {digest}"
        )
    groups, metadata = _read_wheel_metadata(data)
    manifests: dict[str, dict] = {}
    try:
        for target, group in TARGET_GROUPS:
            if group in groups:
                manifests[target] = wheels.inspect_wheel(
                    data,
                    filename=filename,
                    entry_point_group=group,
                    available_distributions=available,
                )
    except wheels.WheelInspectionError as exc:
        raise CatalogError(f"{label}: {exc}") from exc
    if not manifests:
        raise CatalogError(
            f"{label}: the wheel declares neither a {wheels.DESKTOP_ENTRY_POINT_GROUP} "
            f"nor a {wheels.WEB_ENTRY_POINT_GROUP} entry point"
        )
    manifest = next(iter(manifests.values()))
    try:
        same_version = Version(manifest["version"]) == Version(release["version"])
    except InvalidVersion:
        same_version = False
    if not same_version:
        raise CatalogError(f"{label}: the wheel version is {manifest['version']}")
    record = {
        "version": manifest["version"],
        "filename": filename,
        "url": f"wheels/{digest}/{filename}",
        "sha256": digest,
        "size": len(data),
        "requires_python": manifest["requires_python"],
        "requires_dist": [item["requirement"] for item in manifest["dependencies"]],
        "targets": sorted(manifests),
        "source_url": url,
    }
    if "yanked" in release:
        record["yanked"] = release["yanked"]
    package = {
        "distribution": manifest["distribution"],
        "summary": manifest["summary"],
        "license": _check_license(metadata, label),
    }
    return record, package, data


def check_entry(
    path: Path, entry: dict, available: Mapping[str, str]
) -> tuple[dict, dict[str, bytes]]:
    """Check an entry and return its catalog record and wheels by mirror path."""
    official = entry["repository"].split("/")[3].lower() == OFFICIAL_OWNER
    if entry["id"].startswith(OFFICIAL_ID_PREFIX) and not official:
        raise CatalogError(
            f"{path.name}: IDs starting with {OFFICIAL_ID_PREFIX!r} are reserved "
            "for DataLab-Platform repositories"
        )
    versions = [release["version"] for release in entry["releases"]]
    if len(set(versions)) != len(versions):
        raise CatalogError(f"{path.name}: duplicate release versions")
    plugin: dict[str, Any] = {
        "id": entry["id"],
        "name": entry["name"],
        "tier": "official" if official else "community",
        "status": entry.get("status", "active"),
        "repository": entry["repository"],
        "documentation": entry.get("documentation", entry["repository"]),
        "capabilities": entry.get("capabilities", []),
        "keywords": entry.get("keywords", []),
    }
    if "status_reason" in entry:
        plugin["status_reason"] = entry["status_reason"]
    if plugin["status"] == "revoked":
        # Revoked wheels are neither downloaded nor mirrored
        plugin["releases"] = [
            {key: release[key] for key in ("version", "sha256") if key in release}
            for release in entry["releases"]
        ]
        return plugin, {}

    records: list[tuple[Version, dict, dict]] = []
    files: dict[str, bytes] = {}
    for release in entry["releases"]:
        record, package, data = check_release(entry, release, available)
        records.append((Version(record["version"]), record, package))
        files[record["url"]] = data
    records.sort(key=lambda item: item[0], reverse=True)
    distributions = {
        canonicalize_name(package["distribution"]) for _v, _r, package in records
    }
    if len(distributions) != 1:
        raise CatalogError(f"{path.name}: releases come from different distributions")
    plugin.update(records[0][2])
    plugin["releases"] = [record for _version, record, _package in records]
    return plugin, files


def build_catalog(
    entries_dir: Path, available: Mapping[str, str], output: Path | None = None
) -> dict:
    """Check every entry and optionally write the published catalog.

    Raises:
        CatalogError: Listing every rejected entry
    """
    validator = jsonschema.Draft202012Validator(
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    )
    errors: list[str] = []
    plugins: list[dict] = []
    files: dict[str, bytes] = {}
    for path in sorted(entries_dir.glob("*.yaml")):
        try:
            plugin, plugin_files = check_entry(
                path, load_entry(path, validator), available
            )
        except CatalogError as exc:
            errors.append(str(exc))
            continue
        plugins.append(plugin)
        files.update(plugin_files)
    owners: dict[str, str] = {}
    for plugin in plugins:
        if "distribution" not in plugin:
            continue
        key = canonicalize_name(plugin["distribution"])
        if key in owners:
            errors.append(
                f"{plugin['id']}: distribution {plugin['distribution']} is already "
                f"listed by {owners[key]}"
            )
        owners[key] = plugin["id"]
    if errors:
        raise CatalogError("\n".join(errors))
    catalog = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "plugins": sorted(plugins, key=lambda plugin: plugin["name"].lower()),
    }
    if output is not None:
        write_site(output, catalog, files)
    return catalog


def write_site(output: Path, catalog: dict, files: Mapping[str, bytes]) -> None:
    """Write the catalog, its mirrored wheels and a human-readable index."""
    output.mkdir(parents=True, exist_ok=True)
    for relative_path, data in files.items():
        path = output / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (output / "catalog.json").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output / "schema").mkdir(exist_ok=True)
    shutil.copy2(SCHEMA_PATH, output / "schema" / SCHEMA_PATH.name)
    (output / "index.html").write_text(render_index(catalog), encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")


def render_index(catalog: dict) -> str:
    """Return the HTML page listing the catalog plugins."""
    rows = []
    for plugin in catalog["plugins"]:
        installable = [
            release
            for release in plugin["releases"]
            if "url" in release and "yanked" not in release
        ]
        latest = installable[0] if installable else None
        download = "&mdash;"
        if latest and plugin["status"] != "revoked":
            url, version = html.escape(latest["url"]), html.escape(latest["version"])
            download = f'<a href="{url}">{version}</a>'
        description = plugin.get("summary", plugin.get("status_reason", ""))
        targets = ", ".join(latest["targets"]) if latest else ""
        rows.append(
            "<tr>"
            f'<td><a href="{html.escape(plugin["repository"])}">'
            f"{html.escape(plugin['name'])}</a><br><small>{html.escape(plugin['id'])}"
            "</small></td>"
            f"<td>{html.escape(description)}</td>"
            f"<td>{html.escape(targets)}</td>"
            f"<td>{html.escape(plugin['tier'])}<br><small>"
            f"{html.escape(plugin['status'])}</small></td>"
            f"<td>{download}</td>"
            "</tr>"
        )
    table = "\n".join(rows) or '<tr><td colspan="5">No plugin yet.</td></tr>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DataLab plugins</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 64rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border-bottom: 1px solid #ddd; padding: .5rem; text-align: left; }}
small {{ color: #666; }}
</style>
</head>
<body>
<h1>DataLab plugins</h1>
<p>Download a wheel and install it in DataLab with <b>Plugins &gt; Configure
plugins... &gt; Install plugins</b>. Plugins run with the same rights as DataLab:
install only plugins from authors you trust. Machine-readable catalog:
<a href="catalog.json">catalog.json</a>.
Submit a plugin: <a href="https://github.com/DataLab-Platform/plugins">
DataLab-Platform/plugins</a>.</p>
<table>
<thead><tr><th>Plugin</th><th>Description</th><th>Targets</th><th>Tier</th>
<th>Download</th></tr></thead>
<tbody>
{table}
</tbody>
</table>
<p><small>Updated {html.escape(catalog["generated_at"])}</small></p>
</body>
</html>
"""


def report(catalog: dict) -> str:
    """Return a Markdown summary of the checked catalog."""
    lines = [
        "| Plugin | Tier | Status | Version | Targets | SHA-256 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for plugin in catalog["plugins"]:
        for release in plugin["releases"]:
            lines.append(
                f"| {plugin['id']} | {plugin['tier']} | {plugin['status']} "
                f"| {release['version']} | {', '.join(release.get('targets', []))} "
                f"| `{release.get('sha256', '')}` |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the catalog command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="Check catalog entries")
    build_parser = subparsers.add_parser("build", help="Build the published catalog")
    build_parser.add_argument("-o", "--output", type=Path, required=True)
    for subparser in (check_parser, build_parser):
        subparser.add_argument("--entries", type=Path, default=ENTRIES_DIR)
    args = parser.parse_args(argv)
    output = args.output if args.command == "build" else None
    try:
        catalog = build_catalog(args.entries, get_host_distributions(), output)
    except CatalogError as exc:
        print(exc, file=sys.stderr)
        return 1
    summary = report(catalog)
    print(summary)
    if "GITHUB_STEP_SUMMARY" in os.environ:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
            file.write(summary + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

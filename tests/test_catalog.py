# Copyright (c) DataLab Platform Developers, BSD 3-Clause license, see LICENSE file.

"""Tests of the catalog tooling, with release downloads served from memory."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import catalog  # noqa: E402

HOST = {"datalab-platform": "1.4.0", "numpy": "2.1.0"}
DESKTOP_AND_WEB = (
    "[datalab.plugins]\nexample = example_plugin.desktop:ExamplePlugin\n"
    "[datalab.web_plugins]\nexample = example_plugin.web:ExampleWebPlugin\n"
)


def make_wheel(
    distribution: str = "example-plugin",
    version: str = "1.0.0",
    *,
    entry_points: str = DESKTOP_AND_WEB,
    license_expression: str = "BSD-3-Clause",
    requires: tuple[str, ...] = ("datalab-platform>=1.4", "numpy>=1.22"),
) -> tuple[str, bytes]:
    """Return the file name and content of a plugin wheel."""
    stem = f"{distribution.replace('-', '_')}-{version}"
    metadata = [
        "Metadata-Version: 2.4",
        f"Name: {distribution}",
        f"Version: {version}",
        "Summary: Example plugin",
        f"License-Expression: {license_expression}",
        "Requires-Python: >=3.9",
        *(f"Requires-Dist: {requirement}" for requirement in requires),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("example_plugin/__init__.py", "")
        archive.writestr(f"{stem}.dist-info/METADATA", "\n".join(metadata) + "\n")
        archive.writestr(
            f"{stem}.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{stem}.dist-info/entry_points.txt", entry_points)
        archive.writestr(f"{stem}.dist-info/top_level.txt", "example_plugin\n")
    return f"{stem}-py3-none-any.whl", buffer.getvalue()


@pytest.fixture(name="served")
def fixture_served(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """Serve downloads from a dictionary instead of the network."""
    served: dict[str, bytes] = {}

    def fetch(url: str) -> bytes:
        if url not in served:
            raise catalog.CatalogError(f"Cannot download {url}: 404")
        return served[url]

    monkeypatch.setattr(catalog, "fetch", fetch)
    return served


def publish_on_github(served: dict, repository: str, tag: str, wheel) -> str:
    """Serve a GitHub release holding a wheel and return its digest."""
    filename, data = wheel
    asset_url = f"{repository}/releases/download/{tag}/{filename}"
    owner_repo = repository.removeprefix("https://github.com/")
    served[f"https://api.github.com/repos/{owner_repo}/releases/tags/{tag}"] = (
        json.dumps(
            {"assets": [{"name": filename, "browser_download_url": asset_url}]}
        ).encode()
    )
    served[asset_url] = data
    return hashlib.sha256(data).hexdigest()


def write_entry(directory: Path, plugin_id: str, text: str) -> Path:
    """Write a catalog entry file."""
    directory.mkdir(exist_ok=True)
    path = directory / f"{plugin_id}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_github_release_is_mirrored_in_the_published_catalog(
    served: dict, tmp_path: Path
) -> None:
    """A community release is verified, inspected and mirrored."""
    repository = "https://github.com/someone/example-plugin"
    wheel = make_wheel()
    digest = publish_on_github(served, repository, "v1.0.0", wheel)
    entries = tmp_path / "plugins"
    write_entry(
        entries,
        "io.github.someone.example",
        f"""id: io.github.someone.example
name: Example
repository: {repository}
capabilities: [processing]
releases:
  - version: 1.0.0
    sha256: {digest}
""",
    )

    result = catalog.build_catalog(entries, HOST, tmp_path / "site")

    (plugin,) = result["plugins"]
    assert plugin["tier"] == "community"
    assert plugin["distribution"] == "example-plugin"
    assert plugin["license"] == "BSD-3-Clause"
    (release,) = plugin["releases"]
    assert release["url"] == f"wheels/{digest}/{wheel[0]}"
    assert release["targets"] == ["desktop", "web"]
    assert (tmp_path / "site" / release["url"]).read_bytes() == wheel[1]
    published = json.loads((tmp_path / "site" / "catalog.json").read_text("utf-8"))
    assert published["schema_version"] == 1
    assert "Example" in (tmp_path / "site" / "index.html").read_text("utf-8")


def test_missing_digest_reports_the_value_to_declare(
    served: dict, tmp_path: Path
) -> None:
    """Authors copy the digest printed by the check."""
    repository = "https://github.com/someone/example-plugin"
    digest = publish_on_github(served, repository, "v1.0.0", make_wheel())
    entries = tmp_path / "plugins"
    write_entry(
        entries,
        "io.github.someone.example",
        f"id: io.github.someone.example\nname: Example\nrepository: {repository}\n"
        "releases:\n  - version: 1.0.0\n",
    )

    with pytest.raises(catalog.CatalogError, match=f"sha256: {digest}"):
        catalog.build_catalog(entries, HOST)


def test_reserved_ids_require_a_datalab_platform_repository(
    served: dict, tmp_path: Path
) -> None:
    """Only DataLab-Platform repositories may use org.datalab. IDs."""
    entries = tmp_path / "plugins"
    for owner in ("someone", "DataLab-Platform"):
        repository = f"https://github.com/{owner}/example-plugin"
        digest = publish_on_github(served, repository, "v1.0.0", make_wheel())
        write_entry(
            entries,
            "org.datalab.example",
            f"id: org.datalab.example\nname: Example\nrepository: {repository}\n"
            f"releases:\n  - version: 1.0.0\n    sha256: {digest}\n",
        )
        if owner == "someone":
            with pytest.raises(catalog.CatalogError, match="reserved"):
                catalog.build_catalog(entries, HOST)
        else:
            (plugin,) = catalog.build_catalog(entries, HOST)["plugins"]
            assert plugin["tier"] == "official"


def test_pypi_release_and_license_policy(served: dict, tmp_path: Path) -> None:
    """PyPI wheels are accepted; licenses must be OSI-approved."""
    entries = tmp_path / "plugins"
    for license_expression, accepted in (("MIT", True), ("LicenseRef-Custom", False)):
        filename, data = make_wheel(license_expression=license_expression)
        served["https://pypi.org/pypi/example-plugin/1.0.0/json"] = json.dumps(
            {
                "urls": [
                    {
                        "filename": filename,
                        "url": f"https://files.pythonhosted.org/{filename}",
                        "packagetype": "bdist_wheel",
                    }
                ]
            }
        ).encode()
        served[f"https://files.pythonhosted.org/{filename}"] = data
        write_entry(
            entries,
            "io.github.someone.example",
            "id: io.github.someone.example\nname: Example\n"
            "repository: https://github.com/someone/example-plugin\nreleases:\n"
            "  - version: 1.0.0\n    source: pypi\n    distribution: example-plugin\n"
            f"    sha256: {hashlib.sha256(data).hexdigest()}\n",
        )
        if accepted:
            (plugin,) = catalog.build_catalog(entries, HOST)["plugins"]
            assert plugin["license"] == "MIT"
        else:
            with pytest.raises(catalog.CatalogError, match="LicenseRef-Custom"):
                catalog.build_catalog(entries, HOST)


def test_revoked_plugin_is_listed_without_downloading_its_wheels(
    served: dict, tmp_path: Path
) -> None:
    """Clients can warn about a revoked plugin that is never mirrored."""
    entries = tmp_path / "plugins"
    write_entry(
        entries,
        "io.github.someone.example",
        "id: io.github.someone.example\nname: Example\n"
        "repository: https://github.com/someone/example-plugin\nstatus: revoked\n"
        f"status_reason: Malicious code\nreleases:\n  - version: 1.0.0\n"
        f"    sha256: {'a' * 64}\n",
    )

    result = catalog.build_catalog(entries, HOST, tmp_path / "site")

    (plugin,) = result["plugins"]
    assert plugin["status"] == "revoked"
    assert plugin["releases"] == [{"version": "1.0.0", "sha256": "a" * 64}]
    assert not (tmp_path / "site" / "wheels").exists()
    assert not served


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "id: io.github.someone.example\nname: Example\n"
            "repository: https://gitlab.com/someone/example\n"
            "releases:\n  - version: 1.0.0\n",
            "repository",
        ),
        (
            "id: io.github.someone.other\nname: Example\n"
            "repository: https://github.com/someone/example\n"
            "releases:\n  - version: 1.0.0\n",
            "must be named",
        ),
        (
            "id: io.github.someone.example\nname: Example\n"
            "repository: https://github.com/someone/example\nstatus: deprecated\n"
            "releases:\n  - version: 1.0.0\n",
            "status_reason",
        ),
    ],
)
def test_invalid_entries_are_rejected(tmp_path: Path, text: str, message: str) -> None:
    """Entries are validated against the schema before any download."""
    entries = tmp_path / "plugins"
    write_entry(entries, "io.github.someone.example", text)

    with pytest.raises(catalog.CatalogError, match=message):
        catalog.build_catalog(entries, HOST)


def test_wheels_must_install_on_the_latest_datalab(
    served: dict, tmp_path: Path
) -> None:
    """Dependencies not provided by DataLab and duplicated distributions fail."""
    entries = tmp_path / "plugins"
    repository = "https://github.com/someone/example-plugin"
    digest = publish_on_github(
        served, repository, "v1.0.0", make_wheel(requires=("astropy>=6",))
    )
    write_entry(
        entries,
        "io.github.someone.example",
        f"id: io.github.someone.example\nname: Example\nrepository: {repository}\n"
        f"releases:\n  - version: 1.0.0\n    sha256: {digest}\n",
    )
    with pytest.raises(catalog.CatalogError, match="not provided by DataLab"):
        catalog.build_catalog(entries, HOST)

    digest = publish_on_github(served, repository, "v1.0.0", make_wheel())
    for plugin_id in ("io.github.someone.example", "io.github.someone.copy"):
        write_entry(
            entries,
            plugin_id,
            f"id: {plugin_id}\nname: Example\nrepository: {repository}\n"
            f"releases:\n  - version: 1.0.0\n    sha256: {digest}\n",
        )
    with pytest.raises(catalog.CatalogError, match="already listed"):
        catalog.build_catalog(entries, HOST)

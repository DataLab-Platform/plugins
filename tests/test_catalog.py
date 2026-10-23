# Copyright (c) DataLab Platform Developers, BSD 3-Clause license, see LICENSE file.

"""Tests of the catalog tooling, with release downloads served from memory."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from importlib.machinery import ModuleSpec, PathFinder
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
    requires_python: str = ">=3.9",
    package: str = "example_plugin",
    tag: str = "py3-none-any",
) -> tuple[str, bytes]:
    """Return the file name and content of a plugin wheel."""
    stem = f"{distribution.replace('-', '_')}-{version}"
    metadata = [
        "Metadata-Version: 2.4",
        f"Name: {distribution}",
        f"Version: {version}",
        "Summary: Example plugin",
        f"License-Expression: {license_expression}",
        f"Requires-Python: {requires_python}",
        *(f"Requires-Dist: {requirement}" for requirement in requires),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{package}/__init__.py", "")
        archive.writestr(f"{stem}.dist-info/METADATA", "\n".join(metadata) + "\n")
        archive.writestr(
            f"{stem}.dist-info/WHEEL",
            f"Wheel-Version: 1.0\nRoot-Is-Purelib: "
            f"{'true' if tag.endswith('none-any') else 'false'}\nTag: {tag}\n",
        )
        archive.writestr(f"{stem}.dist-info/entry_points.txt", entry_points)
        archive.writestr(f"{stem}.dist-info/top_level.txt", f"{package}\n")
    return f"{stem}-{tag}.whl", buffer.getvalue()


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


@pytest.mark.parametrize("icon", [None, "https://images.example.test/icon"])
def test_github_release_is_mirrored_in_the_published_catalog(
    served: dict, tmp_path: Path, icon: str | None
) -> None:
    """A release is mirrored without downloading its optional icon."""
    repository = "https://github.com/someone/example-plugin"
    wheel = make_wheel()
    digest = publish_on_github(served, repository, "v1.0.0", wheel)
    entries = tmp_path / "plugins"
    icon_field = f"icon: {icon}\n" if icon is not None else ""
    write_entry(
        entries,
        "io.github.someone.example",
        f"""id: io.github.someone.example
name: Example
repository: {repository}
{icon_field}capabilities: [processing]
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
    if icon is None:
        assert "icon" not in plugin
        assert "icon" not in published["plugins"][0]
    else:
        assert plugin["icon"] == icon
        assert published["plugins"][0]["icon"] == icon
    page = (tmp_path / "site" / "index.html").read_text("utf-8")
    if icon is None:
        assert 'class="plugin-icon"' not in page
        assert ">E</span>" in page
    else:
        assert f'<img class="plugin-icon" src="{icon}" alt=""' in page
        assert 'width="48" height="48" referrerpolicy="no-referrer" hidden>' in page
    assert 'data-tier="community" data-targets="desktop web"' in page
    assert f'href="{release["url"]}" download>Download 1.0.0</a>' in page
    assert ">Community</span>" in page
    for name in catalog.PAGE_ASSETS:
        assert (tmp_path / "site" / name).is_file()


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


def module_entry(served: dict, tmp_path: Path, source: bytes) -> tuple[Path, str]:
    """Publish an unchanged Python file as an exact GitHub release asset."""
    repository = "https://github.com/someone/single-file-plugin"
    filename = "datalab_single_probe.py"
    digest = publish_on_github(served, repository, "v1.0", (filename, source))
    entries = tmp_path / "plugins"
    write_entry(
        entries,
        "io.github.someone.single",
        f"""id: io.github.someone.single
name: Single-file example
kind: module
summary: An unchanged historical script
license: BSD-3-Clause
repository: {repository}
capabilities: [processing]
releases:
  - version: "1.0"
    filename: {filename}
    sha256: {digest}
""",
    )
    return entries, filename


def test_single_file_release_is_mirrored_without_execution(
    served: dict, tmp_path: Path
) -> None:
    """A public artifact is usable offline without building a wheel."""
    marker = tmp_path / "must-not-run"
    source = f"open({str(marker)!r}, 'w').write('executed')\n".encode()
    entries, filename = module_entry(served, tmp_path, source)
    site = tmp_path / "site"
    result = catalog.build_catalog(entries, HOST, site)
    (plugin,) = result["plugins"]
    assert plugin["kind"] == "module"
    assert plugin["module"] == filename[:-3]
    assert "distribution" not in plugin
    (release,) = plugin["releases"]
    assert release["targets"] == ["desktop"]
    assert (site / release["url"]).read_bytes() == source
    assert not marker.exists()
    page = (site / "index.html").read_text("utf-8")
    assert "Python file" in page
    assert 'data-targets="desktop"' in page


def test_single_file_published_mirror_installs_with_the_desktop_client(
    served: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = importlib.util.find_spec("datalab")
    plugins = (
        PathFinder.find_spec("datalab.plugins", host.submodule_search_locations)
        if host is not None and host.submodule_search_locations is not None
        else None
    )
    if (
        plugins is None
        or plugins.submodule_search_locations is None
        or PathFinder.find_spec(
            "datalab.plugins.modules", plugins.submodule_search_locations
        )
        is None
    ):
        pytest.skip("Requires the updated Desktop client, not the legacy Qt host")
    marker = tmp_path / "must-not-run"
    source = f"open({str(marker)!r}, 'w').write('executed')\n".encode()
    entries, _filename = module_entry(served, tmp_path, source)
    site = tmp_path / "site"
    catalog.build_catalog(entries, HOST, site)
    # Exercise the actual Desktop client on the generated offline mirror.
    from datalab.plugins.catalog import download_release, read_folder_catalog
    from datalab.plugins.store import InstalledPluginStore

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: pytest.fail("Offline mirror used the network"),
    )
    (offline,), warnings = read_folder_catalog(str(site))
    assert not warnings
    candidate = offline.get_installable_release()
    assert candidate is not None
    download = tmp_path / "download"
    download.mkdir()
    artifact = download_release(candidate, str(download))
    store = InstalledPluginStore(str(tmp_path / "installed"))
    installed = store.install_module(
        artifact,
        expected_sha256=candidate.sha256,
        catalog_id=offline.id,
        catalog_name=offline.name,
        catalog_version=candidate.version,
    ).plugin
    assert installed.version == "1.0"
    assert Path(store.get_path(installed)).read_bytes() == source
    assert not marker.exists()


def test_optional_integration_does_not_import_the_legacy_qt_host(
    served: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DataLab 1.3 has plugins.py, which imports Qt before a child lookup."""
    original = PathFinder.find_spec
    legacy = ModuleSpec("datalab.plugins", loader=None, is_package=False)

    def find_spec(name, path=None, target=None):
        if name == "datalab.plugins":
            return legacy
        return original(name, path, target)

    monkeypatch.setattr(PathFinder, "find_spec", find_spec)
    with pytest.raises(pytest.skip.Exception, match="updated Desktop client"):
        test_single_file_published_mirror_installs_with_the_desktop_client(
            served, tmp_path, monkeypatch
        )


@pytest.mark.parametrize(
    ("source", "detail"),
    [
        (b"", "empty"),
        (b"class Broken(", "Invalid Python"),
        (
            b"from datalab.plugins import PluginInfo\n"
            b"class Example:\n"
            b'    PLUGIN_INFO = PluginInfo(id="org.example.other", version="1.0")',
            "plugin_id",
        ),
        (
            b"from datalab.plugins import PluginInfo\n"
            b"class Example:\n"
            b'    PLUGIN_INFO = PluginInfo(id="io.github.someone.single", '
            b'version="2.0")',
            "version",
        ),
    ],
)
def test_single_file_invalid_source_or_conflicting_metadata(
    served: dict, tmp_path: Path, source: bytes, detail: str
) -> None:
    entries, _filename = module_entry(served, tmp_path, source)
    with pytest.raises(catalog.CatalogError, match=detail):
        catalog.build_catalog(entries, HOST)


def test_single_file_requires_exact_asset_and_has_no_dependency_installation(
    served: dict, tmp_path: Path
) -> None:
    entries, filename = module_entry(served, tmp_path, b"VALUE = 1\n")
    path = entries / "io.github.someone.single.yaml"
    original = path.read_text("utf-8")
    path.write_text(original.replace(filename, "datalab_missing.py"), encoding="utf-8")
    with pytest.raises(catalog.CatalogError, match="expected the module asset"):
        catalog.build_catalog(entries, HOST)
    path.write_text(
        original.replace(
            f"    filename: {filename}",
            f"    filename: {filename}\n    source: pypi\n    distribution: anything",
        ),
        encoding="utf-8",
    )
    with pytest.raises(catalog.CatalogError, match="github"):
        catalog.build_catalog(entries, HOST)


def test_module_inspector_copy_matches_desktop() -> None:
    """The shared module contract must remain byte-for-byte identical."""
    desktop = (
        Path(__file__).resolve().parents[2]
        / "DataLab"
        / "datalab"
        / "plugins"
        / "modules.py"
    )
    if desktop.is_file():
        assert (
            catalog.ROOT / "tools" / "modules.py"
        ).read_bytes() == desktop.read_bytes()


def test_additional_desktop_dependencies_are_verified_and_mirrored(
    served: dict, tmp_path: Path
) -> None:
    """Ordinary native wheels are mirrored, without enabling Web dependencies."""
    repository = "https://github.com/someone/example-plugin"
    dependency_name, dependency_bytes = make_wheel(
        "additional-helper",
        package="additional_helper",
        tag="cp311-cp311-win_amd64",
        entry_points="",
        requires=(),
    )
    dependency_url = f"https://files.example.test/{dependency_name}"
    served[dependency_url] = dependency_bytes
    digest = publish_on_github(
        served,
        repository,
        "v1.0.0",
        make_wheel(
            entry_points=(
                "[datalab.plugins]\nexample = example_plugin.desktop:ExamplePlugin\n"
            ),
            requires=("datalab-platform>=1.4", "additional-helper>=1"),
        ),
    )
    entries = tmp_path / "plugins"
    write_entry(
        entries,
        "io.github.someone.example",
        f"""id: io.github.someone.example
name: Example
repository: {repository}
releases:
  - version: 1.0.0
    sha256: {digest}
    dependency_wheels:
      - filename: {dependency_name}
        url: {dependency_url}
        sha256: {hashlib.sha256(dependency_bytes).hexdigest()}
""",
    )
    result = catalog.build_catalog(entries, HOST, tmp_path / "site")
    (release,) = result["plugins"][0]["releases"]
    assert release["targets"] == ["desktop"]
    (artifact,) = release["dependency_wheels"]
    assert (tmp_path / "site" / artifact["url"]).read_bytes() == dependency_bytes
    page = (tmp_path / "site" / "index.html").read_text("utf-8")
    assert "Additional Desktop packages" in page
    assert f'href="{artifact["url"]}" download>{dependency_name}</a>' in page
    served[dependency_url] = b"changed"
    with pytest.raises(catalog.CatalogError, match="dependency digest"):
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


@pytest.mark.parametrize("icon", [None, "https://images.example.test/icon.svg"])
def test_revoked_plugin_is_listed_without_downloading_its_wheels(
    served: dict, tmp_path: Path, icon: str | None
) -> None:
    """Clients can warn about a revoked plugin that is never mirrored."""
    entries = tmp_path / "plugins"
    icon_field = f"icon: {icon}\n" if icon is not None else ""
    write_entry(
        entries,
        "io.github.someone.example",
        "id: io.github.someone.example\nname: Example\n"
        "repository: https://github.com/someone/example-plugin\n"
        f"{icon_field}status: revoked\n"
        f"status_reason: Malicious code\nreleases:\n  - version: 1.0.0\n"
        f"    sha256: {'a' * 64}\n",
    )

    result = catalog.build_catalog(entries, HOST, tmp_path / "site")

    (plugin,) = result["plugins"]
    assert plugin["status"] == "revoked"
    assert plugin.get("icon") == icon
    assert plugin["releases"] == [{"version": "1.0.0", "sha256": "a" * 64}]
    assert not (tmp_path / "site" / "wheels").exists()
    assert not served
    page = (tmp_path / "site" / "index.html").read_text("utf-8")
    assert '<p class="notice status-revoked">Malicious code</p>' in page
    assert "No installable release" in page
    assert "download>" not in page


def test_page_escapes_metadata_read_from_wheels() -> None:
    """Wheel summaries are untrusted and must not inject markup."""
    page = catalog.render_index(
        {
            "generated_at": "2026-10-08T13:29:23+00:00",
            "plugins": [
                {
                    "id": "io.github.someone.example",
                    "name": "Camera & Detector",
                    "tier": "community",
                    "status": "active",
                    "repository": "https://github.com/someone/example",
                    "icon": 'https://example.test/icon?x="><svg/onload=alert(1)>&v=1',
                    "summary": '<script>alert("x")</script>',
                    "keywords": ['"><img src=x>'],
                    "releases": [],
                }
            ],
        }
    )

    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page
    assert "<img src=x>" not in page
    assert "<svg/onload" not in page
    assert (
        'src="https://example.test/icon?x=&quot;&gt;&lt;svg/onload=alert(1)&gt;&amp;v=1"'
        in page
    )
    assert '>CD<img class="plugin-icon"' in page
    assert "Camera &amp; Detector</a>" in page
    assert "1 plugin<" in page
    assert '<time datetime="2026-10-08T13:29:23+00:00">2026-10-08 13:29 UTC' in page


def test_empty_catalog_page_invites_submissions() -> None:
    """The page of an empty catalog links to the contribution guide."""
    page = catalog.render_index(
        {"generated_at": "2026-10-08T13:29:23+00:00", "plugins": []}
    )

    assert f'href="{catalog.CONTRIBUTING_URL}">Submit the first one' in page
    assert 'class="card"' not in page


@pytest.mark.parametrize("asset", ["style.css", "catalog.js"])
def test_page_versions_assets_by_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, asset: str
) -> None:
    """A changed asset gets a new URL instead of reusing a cached old file."""
    page_dir = tmp_path / "page"
    page_dir.mkdir()
    for name in ("index.html", "style.css", "catalog.js"):
        (page_dir / name).write_bytes((catalog.PAGE_DIR / name).read_bytes())
    monkeypatch.setattr(catalog, "PAGE_DIR", page_dir)
    payload = {"generated_at": "2026-10-08T13:29:23+00:00", "plugins": []}
    urls = {}
    for name in ("style.css", "catalog.js"):
        version = hashlib.sha256((page_dir / name).read_bytes()).hexdigest()[:12]
        urls[name] = f"{name}?v={version}"
    page = catalog.render_index(payload)
    assert all(url in page for url in urls.values())
    assert catalog.render_index(payload) == page

    path = page_dir / asset
    path.write_bytes(path.read_bytes() + b"\n/* Updated */\n")
    updated = catalog.render_index(payload)
    version = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    assert f"{asset}?v={version}" in updated
    assert urls[asset] not in updated
    other = "catalog.js" if asset == "style.css" else "style.css"
    assert urls[other] in updated


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


@pytest.mark.parametrize(
    "icon",
    [
        "",
        "http://example.test/icon.svg",
        "javascript:alert(1)",
        "data:image/svg+xml,<svg/>",
        "icons/plugin.svg",
        "//example.test/icon.svg",
        "https://",
        "https:///icon.svg",
        "https://example.test/icon with spaces.svg",
    ],
)
def test_invalid_icon_urls_are_rejected(tmp_path: Path, icon: str) -> None:
    """Only absolute HTTPS image URLs are accepted, before any download."""
    entries = tmp_path / "plugins"
    write_entry(
        entries,
        "io.github.someone.example",
        "id: io.github.someone.example\nname: Example\n"
        "repository: https://github.com/someone/example\n"
        f"icon: {json.dumps(icon)}\nreleases:\n  - version: 1.0.0\n",
    )

    with pytest.raises(catalog.CatalogError, match="icon"):
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


@pytest.mark.parametrize(
    ("requires_python", "error"),
    [
        (">=3.10", None),
        (">=3.12", None),
        (">=3.13", r"\(web, Python 3.12\)"),
        ("<3.10", r"\(desktop, Python 3.10, .*3.14\)"),
        ("==3.9.*", r"\(desktop, Python 3.10, .*3.14\)"),
    ],
)
def test_targets_are_checked_with_their_python_versions(
    served: dict, tmp_path: Path, requires_python: str, error: str | None
) -> None:
    """Wheels are judged by the Python of each target, not by the CI interpreter."""
    repository = "https://github.com/someone/example-plugin"
    wheel = make_wheel(requires_python=requires_python)
    digest = publish_on_github(served, repository, "v1.0.0", wheel)
    entries = tmp_path / "plugins"
    write_entry(
        entries,
        "io.github.someone.example",
        f"id: io.github.someone.example\nname: Example\nrepository: {repository}\n"
        f"releases:\n  - version: 1.0.0\n    sha256: {digest}\n",
    )

    if error is None:
        (plugin,) = catalog.build_catalog(entries, HOST)["plugins"]
        assert plugin["releases"][0]["targets"] == ["desktop", "web"]
        assert plugin["releases"][0]["requires_python"] == requires_python
    else:
        with pytest.raises(catalog.CatalogError, match=error):
            catalog.build_catalog(entries, HOST)


def test_wheels_module_is_a_verbatim_copy_of_datalab() -> None:
    """The catalog must judge a wheel exactly as DataLab and DataLab-Web do."""
    root = Path(__file__).resolve().parents[1]
    upstream = root.parent / "DataLab" / "datalab" / "plugins" / "wheels.py"
    if not upstream.is_file():
        pytest.skip("no sibling DataLab checkout")

    def read(path: Path) -> str:
        return path.read_text(encoding="utf-8").replace("\r\n", "\n")

    assert read(root / "tools" / "wheels.py") == read(upstream)

# DataLab plugins

Catalog of plugins for [DataLab](https://datalab-platform.com/) desktop and [DataLab-Web](https://datalab-platform.com/web/), published at <https://datalab-platform.com/plugins/>.

## Installing a plugin

In DataLab 1.4 or later, open **Plugins > Configure plugins...**, select the **Available plugins** tab and click **Install**: DataLab downloads the plugin and checks it against the catalog.

To install a downloaded file instead (also with the standalone version of DataLab):

1. Open <https://datalab-platform.com/plugins/> and download the plugin artifact: a wheel (`.whl`) or, for a **Python file** entry, the unchanged `datalab_<name>.py` file.
2. In DataLab, open **Plugins > Configure plugins...**, go to the **Install plugins** tab and click **Install from file...**.

If the plugin lists **Additional Desktop packages**, download the wheels for your Python version and platform into a `dependencies` folder next to the plugin wheel. A complete local catalog with these files can be installed without Internet access, including with the standalone application. DataLab shows the additional packages before confirmation, never replaces existing package versions, and activates the added dependencies after a restart. Uninstalling a plugin keeps its shared dependencies.

A plugin runs with the same rights as DataLab: it may read and modify your files. Install only plugins from authors you trust. Being listed here does not mean that a plugin was audited.

Single-file plugins use libraries already available in Desktop and do not install additional packages. A module may be distributed without a packaging project; source organization does not determine its functional capabilities. Known versions can be updated; local scripts without a version use their digest and offer explicit replacement when changed.

## Publishing a plugin

Any author may list a plugin by opening a pull request that adds one small YAML file to the [plugins](plugins) folder. See [CONTRIBUTING.md](CONTRIBUTING.md).

## How the catalog works

- Each plugin is described by `plugins/<id>.yaml`, validated against [schema/plugin-entry.schema.json](schema/plugin-entry.schema.json).
- Desktop entries may use `kind: module` with an exact `.py` GitHub release asset, summary, license and version. The source can remain unchanged: available metadata is read statically, and the entry supplies what cannot be determined without execution. The page labels these artifacts **Python file**, and does not include them in the Web filter.
- For each wheel release, the CI downloads the wheel from the GitHub release (or from PyPI), checks its SHA-256 digest against the entry, and inspects it without running its code: pure-Python plugin, `datalab.plugins` (desktop) and/or `datalab.web_plugins` (web) entry points, declared requirements and OSI-approved license. Desktop wheels may also list reviewed dependency wheels, including native wheels, which are verified and mirrored with the plugin. Web requirements must still be provided by its host. Python compatibility is checked per target: a desktop plugin must install on at least one Python version supported by DataLab (3.9 to 3.14), a web plugin on the Python of the Pyodide release used by DataLab-Web (3.12). DataLab resolves the complete dependency graph against the actual installation; publication metadata checks are not a functional certification of every platform.
- When a pull request is merged, the catalog is rebuilt and published: `catalog.json`, copies of wheels in `wheels/<sha256>/` and modules in `modules/<sha256>/`, and a human-readable `index.html`. All artifacts are downloaded again and must still match the reviewed digests.
- The page uses content-versioned URLs for its stylesheet and script, so browsers fetch updated assets instead of reusing an older cached version.
- Plugins from DataLab-Platform repositories are marked **official**, others **community**. IDs starting with `org.datalab.` are reserved for official plugins.

## Catalog format

`catalog.json` (`schema_version` 1) lists `plugins`, each with `id`, `name`, `tier` (`official` or `community`), `status` (`active`, `deprecated` or `revoked`, with `status_reason`), `repository`, `documentation`, `capabilities`, `keywords`, `distribution`, `summary`, `license` and `releases`. Releases are sorted from the newest and give `version`, `filename`, `url`, `sha256`, `size`, `requires_python`, `requires_dist`, `targets` (`desktop`, `web`), `source_url` and, when withdrawn, `yanked` (reason). Release URLs are relative to `catalog.json`, so that the catalog can be mirrored on a private server. Revoked plugins keep only the version and digest of their releases.

A release may additionally list `dependency_wheels`, each with `filename`, mirrored `url`, `sha256` and `size`, plus upstream and metadata information. The field is optional: existing catalogs and self-contained plugins keep their format and behavior. Dependency artifacts belong to Desktop only; this does not add a package-index installer to DataLab-Web.

For modules, a plugin record adds `kind: "module"` and its real import name in `module`, without a fictitious `distribution`. Its releases still use `filename`, `url`, `sha256`, `size`, version, optional Python constraint and the `desktop` target, but cannot include dependency wheels. Existing wheel records need no new fields. An older wheel-only client skips module artifacts as non-installable.

Plugins may also declare an optional `icon` HTTPS URL, used only by the web page. The image is loaded directly by the browser, not downloaded or mirrored by the catalog build. Initials remain the fallback if the image is absent or unavailable, including on a private mirror without access to the image host. See [CONTRIBUTING.md](CONTRIBUTING.md#optional-icon).

## Development

```powershell
pip install -r requirements.txt datalab-platform
python -m pytest tests
python tools/catalog.py check
python tools/catalog.py build --output site
```

[tools/wheels.py](tools/wheels.py) is a verbatim copy of `datalab/plugins/wheels.py` from DataLab: update both together. A test compares them when a sibling DataLab checkout is available. Update `TARGET_PYTHONS` in [tools/catalog.py](tools/catalog.py) when DataLab supports a new Python version or DataLab-Web moves to another Pyodide release.

[tools/modules.py](tools/modules.py) is likewise copied from `datalab/plugins/modules.py`, with an equality test. The module inspector has no Qt dependency and never imports the artifact. The optional Desktop mirror integration test requires the updated client; the remaining publication tests also run against the latest published DataLab reference environment.

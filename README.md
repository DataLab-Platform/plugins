# DataLab plugins

Catalog of plugins for [DataLab](https://datalab-platform.com/) desktop and [DataLab-Web](https://datalab-platform.com/web/), published at <https://datalab-platform.com/plugins/>.

## Installing a plugin

In DataLab 1.4 or later, open **Plugins > Configure plugins...**, select the **Available plugins** tab and click **Install**: DataLab downloads the plugin and checks it against the catalog.

To install a downloaded file instead (also with the standalone version of DataLab):

1. Open <https://datalab-platform.com/plugins/> and download the wheel (`.whl`) of the plugin.
2. In DataLab, open **Plugins > Configure plugins...**, go to the **Install plugins** tab and click **Install from file...**.

A plugin runs with the same rights as DataLab: it may read and modify your files. Install only plugins from authors you trust. Being listed here does not mean that a plugin was audited.

## Publishing a plugin

Any author may list a plugin by opening a pull request that adds one small YAML file to the [plugins](plugins) folder. See [CONTRIBUTING.md](CONTRIBUTING.md).

## How the catalog works

- Each plugin is described by `plugins/<id>.yaml`, validated against [schema/plugin-entry.schema.json](schema/plugin-entry.schema.json).
- For each release, the CI downloads the wheel from the GitHub release (or from PyPI), checks its SHA-256 digest against the entry, and inspects it without running its code, with the same rules as DataLab: pure-Python wheel, `datalab.plugins` (desktop) and/or `datalab.web_plugins` (web) entry points, dependencies provided by the latest DataLab release, OSI-approved license.
- When a pull request is merged, the catalog is rebuilt and published: `catalog.json`, a copy of every wheel in `wheels/<sha256>/`, and a human-readable `index.html`. Wheels are downloaded again at each build and must still match the reviewed digests.
- Plugins from DataLab-Platform repositories are marked **official**, others **community**. IDs starting with `org.datalab.` are reserved for official plugins.

## Catalog format

`catalog.json` (`schema_version` 1) lists `plugins`, each with `id`, `name`, `tier` (`official` or `community`), `status` (`active`, `deprecated` or `revoked`, with `status_reason`), `repository`, `documentation`, `capabilities`, `keywords`, `distribution`, `summary`, `license` and `releases`. Releases are sorted from the newest and give `version`, `filename`, `url`, `sha256`, `size`, `requires_python`, `requires_dist`, `targets` (`desktop`, `web`), `source_url` and, when withdrawn, `yanked` (reason). Release URLs are relative to `catalog.json`, so that the catalog can be mirrored on a private server. Revoked plugins keep only the version and digest of their releases.

## Development

```powershell
pip install -r requirements.txt datalab-platform
python -m pytest tests
python tools/catalog.py check
python tools/catalog.py build --output site
```

[tools/wheels.py](tools/wheels.py) is a verbatim copy of `datalab/plugins/wheels.py` from DataLab: update both together.

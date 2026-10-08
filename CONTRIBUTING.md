# Contributing a plugin to the catalog

Listing a plugin takes a public GitHub repository, a GitHub release holding the wheel, and a pull request adding one YAML file. No PyPI account is needed.

## Requirements

- The source code is public on GitHub and licensed under an OSI-approved license, declared in the wheel metadata (`license = "..."` in `pyproject.toml` with setuptools 77 or later). Accepted licenses: 0BSD, AFL-3.0, Apache-2.0, BSD-2-Clause, BSD-3-Clause, BSL-1.0, CECILL-2.1, EPL-2.0, EUPL-1.2, GPL-2.0 and 3.0, ISC, LGPL-2.1 and 3.0, MIT, MPL-2.0, PSF-2.0, Unlicense, Zlib.
- The plugin is distributed as a single pure-Python wheel (`*-py3-none-any.whl`), without compiled code.
- The wheel declares its plugin class in the `datalab.plugins` entry-point group (DataLab desktop) and/or `datalab.web_plugins` (DataLab-Web).
- Its dependencies are all provided by DataLab (for example `datalab-platform`, `sigima`, `numpy`, `scipy`). Other packages cannot be installed together with a plugin.
- The plugin ID is the `id` of the plugin's `PluginInfo`. Prefer a reverse-domain name that you control, such as `io.github.<your-account>.<plugin>`. The `org.datalab.` prefix is reserved for DataLab-Platform plugins.

Projects created with `datalab-plugin create` already follow these rules.

## Submitting a plugin

1. Build the wheel (`python -m build`) and attach it to a GitHub release of your repository, tagged `v<version>` (for example `v1.0.0`).
2. Fork this repository and add `plugins/<id>.yaml`:

   ```yaml
   # yaml-language-server: $schema=../schema/plugin-entry.schema.json
   id: io.github.alice.spectrum-tools
   name: Spectrum Tools
   repository: https://github.com/alice/datalab-spectrum-tools
   capabilities: [processing]
   keywords: [spectroscopy, baseline]
   releases:
     - version: 1.0.0
   ```

3. Open a pull request. The CI downloads and checks the wheel. The first run fails and prints the line to add to the release, such as `sha256: 3f2a...`: add it and push again.

A wheel published on PyPI may be listed instead of a GitHub release asset, with `source: pypi` and `distribution: <name>` in the release.

## Publishing a new version

Add the version, with its `sha256`, at the end of `releases` and open a pull request. Every new version is reviewed before it is published: keep the previous versions listed.

## Withdrawing a version or a plugin

- `yanked: <reason>` in a release tells DataLab not to install it.
- `status: deprecated` with a `status_reason` keeps the plugin installable with a warning.
- `status: revoked` with a `status_reason` removes the downloads; DataLab warns users who installed the plugin. Maintainers revoke plugins that are malicious or seriously broken.

## Review

Maintainers check that the entry is consistent, that the plugin matches its description, and that nothing in the repository is obviously harmful. This review is not a security audit: users are told to install only plugins from authors they trust.

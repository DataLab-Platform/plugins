## Plugin

- Entry file: `plugins/<id>.yaml`
- Repository:
- What the plugin does (one sentence):

## Checklist

- [ ] The plugin ID is the `id` of the plugin's `PluginInfo`, and the file is named after it.
- [ ] The wheel is pure Python (`*-py3-none-any.whl`) and attached to the GitHub release (or published on PyPI).
- [ ] The wheel declares a `datalab.plugins` and/or `datalab.web_plugins` entry point.
- [ ] The wheel depends only on packages provided by DataLab, listed in `dependency_wheels`, or declared in `environment_requirements` (Desktop packages that users install in DataLab's Python environment).
- [ ] The repository is public and the license is OSI-approved.
- [ ] I tested the plugin with **Plugins > Configure plugins... > From file** in DataLab.

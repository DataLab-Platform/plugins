## Plugin

- Entry file: `plugins/<id>.yaml`
- Repository:
- What the plugin does (one sentence):

## Checklist

- [ ] The plugin ID is the `id` of the plugin's `PluginInfo`, and the file is named after it.
- [ ] The wheel is pure Python (`*-py3-none-any.whl`) and attached to the GitHub release (or published on PyPI).
- [ ] The wheel declares a `datalab.plugins` and/or `datalab.web_plugins` entry point.
- [ ] The wheel depends only on packages provided by DataLab.
- [ ] The repository is public and the license is OSI-approved.
- [ ] I tested the wheel with **Plugins > Configure plugins... > Install plugins** in DataLab.

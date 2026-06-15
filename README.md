# datumctl-plugins

The official plugin index for [datumctl](https://github.com/datum-cloud/datumctl), the CLI for [Datum Cloud](https://datum.net).

## Available plugins

| Name | Description |
|------|-------------|
| [compute](plugins/compute.yaml) | Deploy and manage containerized workloads on Datum Cloud |
| [inventory](plugins/inventory.yaml) | Browse and populate the Datum Cloud inventory graph (typed nodes and edges) |

## Installing a plugin

```sh
datumctl plugin install compute
```

## Submitting a plugin

1. Add the `datumctl-plugin` topic to your GitHub repository.
2. Open a PR adding `plugins/<your-plugin-name>.yaml` following the [schema](schema/plugin-v1alpha1.json).
3. CI will validate your manifest: schema, URI resolution, and SHA256 verification.
4. Once merged, `index.yaml` is regenerated automatically.

## Plugin manifest format

```yaml
apiVersion: datumctl.datum.net/v1alpha1
kind: Plugin
metadata:
  name: my-plugin          # short name: datumctl plugin install my-plugin
spec:
  shortDescription: One-line description shown in datumctl plugin search
  homepage: https://github.com/your-org/datumctl-my-plugin
  version: v1.0.0
  platforms:
    - selector:
        matchLabels:
          os: linux
          arch: amd64
      uri: https://github.com/your-org/datumctl-my-plugin/releases/download/v1.0.0/datumctl-my-plugin_Linux_x86_64.tar.gz
      sha256: <lowercase hex sha256 of the archive>
```

The binary inside each archive must be named `datumctl-<name>` (or `datumctl-<name>.exe` on Windows).

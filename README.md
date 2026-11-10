# Datum Cloud plugins

The official plugin catalog for [datumctl](https://github.com/datum-cloud/datumctl), the CLI for [Datum Cloud](https://datum.net). `datumctl` reads this catalog by default, so every plugin listed here installs by name with no setup.

## Available plugins

| Name | Description |
|------|-------------|
| [alb](plugins/alb.yaml) | Create and manage Application Load Balancers on Datum Cloud |
| [assistant](plugins/assistant.yaml) | Chat with Patch, the Datum Cloud assistant, and browse your conversation history |
| [billing](plugins/billing.yaml) | View metered usage and spend for your organization or project |
| [compute](plugins/compute.yaml) | Deploy and manage containerized workloads on Datum Cloud |
| [dns](plugins/dns.yaml) | Manage DNS zones and records on Datum Cloud |
| [ipam](plugins/ipam.yaml) | Manage IP address space (pools and prefixes) across the platform |
| [network](plugins/network.yaml) | Create and manage networks and network services on Datum Cloud |
| [search](plugins/search.yaml) | Search for resources across the platform by kind, name, and project scope |
| [telemetry](plugins/telemetry.yaml) | Query logs for your Datum Cloud projects |

## Using this catalog

```sh
# Search and install
datumctl plugin search compute
datumctl plugin install compute

# Or browse interactively
datumctl plugin browse
```

Once installed, a plugin runs as a `datumctl` subcommand:

```sh
datumctl compute --help
```

Plugins for the Milo platform live in the [Milo catalog](https://github.com/milo-os/cli-plugins), which you add with `datumctl plugin index add milo-os milo-os/cli-plugins`.

## Submitting a plugin

1. Add the `datumctl-plugin` topic to your plugin's GitHub repository.
2. Open a pull request adding `plugins/<your-plugin-name>.yaml`, following the [schema](https://github.com/datum-cloud/actions/blob/v1.27.1/scripts/plugin-catalog/schema/plugin-v1alpha1.json). That's the only file you edit.
3. CI validates every manifest against the schema and `datumctl plugin index validate`, and checks that each changed download URL resolves and matches its SHA256. A weekly health check re-verifies the whole catalog.
4. On merge, [`index.yaml`](index.yaml), the file `datumctl` actually reads, is rebuilt from `plugins/*.yaml` through a pull request. You never edit it by hand.

New releases reach this catalog through the [`update-plugin-index`](https://github.com/datum-cloud/actions/blob/main/docs/update-plugin-index/README.md) action in your release workflow, and a daily scan opens an update for any release that action missed.

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

### Binary naming

The binary inside each archive must be named `datumctl-<name>` or `milo-<name>` (add `.exe` on Windows).
datumctl recognizes both prefixes, so a plugin built for the milo-os platform installs from this catalog without a second, datumctl-branded build.

To explicitly select the binary to install, set `files` for each platform, as in the [IPAM manifest](plugins/ipam.yaml):

```yaml
      files:
        - from: milo-ipam  # milo-ipam.exe on Windows
```

## How this catalog runs

Validation, the index rebuild, the daily release scan, and the weekly health check all come from the shared [plugin catalog workflows](https://github.com/datum-cloud/actions/blob/main/docs/plugin-catalog/README.md) in `datum-cloud/actions`.

---
description: "Self-hosted GitHub Actions runners on the cluster: one Actions Runner Controller scale set per repository, a pod per job, nothing on the LAN."
---

# GitHub runners

Linux jobs of a private repository run here instead of on GitHub's hosted
runners, whose minutes a free organisation pays for. The
[Actions Runner Controller](https://github.com/actions/actions-runner-controller)
is two components: `arc-controller` runs the controller and its CRDs,
`arc-runners` one scale set per repository. A job gets a
fresh runner pod that exits when the job ends. macOS jobs cannot run here and
stay on GitHub.

## At a glance

| | |
| --- | --- |
| Scale sets | `homelab` on `somuch-cloud/shotapp` |
| Workflow label | `runs-on: homelab` |
| Depends on | [OpenBao](openbao.md) for the GitHub App in `kv/arc-runners/github-app` |
| If it is down | Jobs on `runs-on: homelab` queue on GitHub until a runner registers; nothing else notices |
| Namespaces | `arc-systems` for the controller and the listeners, `arc-runners` for the runner and job pods |
| Files | `payload/platform/arc-controller/`, `payload/platform/arc-runners/`, and the two namespaces' files under `payload/platform/security/` |

## Configuration

| Setting | Why |
| --- | --- |
| Controller and scale set chart on the same `targetRevision` | The controller deletes an `AutoscalingRunnerSet` whose chart version differs from its own, and Argo CD recreates it, in a loop; Renovate moves both in one PR |
| `githubConfigUrl` on the repository, not the organisation | No other repository in the organisation can schedule code onto the cluster |
| `githubConfigSecret: github-app` | A GitHub App rather than a PAT: no expiry and a token scoped to the installed repository — see [GitHub App](#github-app) |
| `controllerServiceAccount` spelled out | The chart finds the controller with Helm `lookup`, which Argo CD does not run |
| `minRunners: 0`, `maxRunners: 10` | No idle pods between pushes; ten lets several pull requests run their parallel Linux jobs at once without queueing, while the requests stay a fraction of the workers' memory |
| `containerMode: kubernetes-novolume` | A job with `services:` or `container:` gets its own pod, started by the container hook through the API server. No privileged Docker daemon and no PVC per job |
| `ACTIONS_RUNNER_REQUIRE_JOB_CONTAINER: "false"` | A job without `container:` runs in the runner pod itself, as on a hosted runner |
| The namespace enforces `baseline` | The runner image uses `sudo`, and a service container such as PostgreSQL starts as root; neither passes `restricted` |
| The runner image as a full reference with a digest | Renovate tracks it; the chart passes the same image to the hook for the job pod's init container |
| `resources`: 1Gi requested, 4Gi limit | Unmeasured; a test suite with a database beside it fits, and the limit stops a runaway build before it starves the node |
| A second scale set, `homelab-wait`, for `ci-ok` alone | The wait holds its runner until every other check on the commit has finished; on the shared set, as many open pull requests as `maxRunners` fill it with waits and the checks they wait for never start. It runs no build, so it has no container mode and a small request |

### Controller

The `arc-controller` values, and why:

| Setting | Why |
| --- | --- |
| `flags.watchSingleNamespace: arc-runners` | The controller gets a Role in this namespace instead of a ClusterRole over every namespace |
| `logLevel: info`, `logFormat: json` | The chart's default is `debug`, which logs every reconcile; JSON is what Loki parses |
| Restricted security context on the controller and the listener | Both are distroless and run as non-root; the namespace enforces `baseline` and audits `restricted` |

### Network policy

Every rule in
`payload/platform/security/network-policies/arc-runners.yaml`. Unlike the
other namespaces, egress is every public address rather than a list of names.

| Rule | Why |
| --- | --- |
| Ingress from the namespace and the node only | Runners and job pods serve nothing; the listener in `arc-systems` reaches GitHub, not the runners |
| Egress to the API server | The container hook creates the job pod and execs each step into it |
| Egress to `0.0.0.0/0` on 80 and 443, minus the private, carrier-grade and link-local ranges | A job downloads whatever its workflow names (actions, toolchains, packages), which no name list keeps up with. Cutting the private ranges keeps the LAN, the nodes and every in-cluster service out of reach |

The controller's and listeners' egress, GitHub's API and the Actions service
only, is in `payload/platform/security/network-policies/arc-systems.yaml`.

## GitHub App

One app per organisation, installed on each repository that has a scale set.

1. Create the app under the organisation's **Settings → Developer settings →
   GitHub Apps → New GitHub App**: any name and homepage URL, **Webhook →
   Active** unticked, **Repository permissions → Administration: Read and
   write**, **Metadata: Read-only**, installable on this account only.
2. On the app's page, note the **App ID** and **Generate a private key**;
   the `.pem` file downloads.
3. **Install App** → **Only select repositories** → the repository. The
   installation ID is the number at the end of the installation's URL.
4. Store all three:

   ```bash
   make bao-secrets    # answers the arc-runners/github-app prompts
   ```

5. Push the secret through and wait for the listener:

   ```bash
   kubectl -n arc-runners annotate externalsecret github-app force-sync="$(date +%s)" --overwrite
   kubectl -n arc-systems get pods -l app.kubernetes.io/component=runner-scale-set-listener
   ```

The scale set then appears under the repository's **Settings → Actions →
Runners**.

## Usage

A job opts in with its label:

```yaml
runs-on: homelab
```

| Job shape | What runs where |
| --- | --- |
| Steps only | In the runner pod, on the runner image |
| `container:` | A job pod from that image; steps exec into it |
| `services:` | Needs a `container:` too; the services join the job pod and answer on `localhost` |

The runner image is GitHub's minimal
[`actions-runner`](https://github.com/actions/runner/blob/main/images/Dockerfile),
not the hosted `ubuntu-latest`: `git`, `curl`, `jq`, `unzip` and `sudo`, and
no language toolchains. A job installs what it needs, pinned, or uses a
`setup-*` action.

To add a repository, install the app on it and add a second chart source to
`application.yaml` with its own `releaseName`, `githubConfigUrl` and
`runnerScaleSetName`.

## Health check

```bash
kubectl -n arc-runners get autoscalingrunnerset,ephemeralrunner
kubectl -n arc-systems get pods
kubectl -n arc-systems logs -l app.kubernetes.io/component=runner-scale-set-listener --tail=50
```

A job waiting on `runs-on: homelab` with no runner pod in `arc-runners`
means the listener is not getting it; its log and the controller's say why.

## Pitfalls

- **`Job Container is required.`** A job with `services:` and no
  `container:`. The hook runs services only beside a job container.
- **No Docker daemon.** The image carries the `docker` CLI, but nothing
  answers it: `docker build` and `docker run` steps fail.
- **No listener until the app is in OpenBao.** An empty
  `kv/arc-runners/github-app` syncs cleanly; the controller then fails to
  register the scale set with GitHub and says so in its log.
- **Removing a scale set takes the controller.** Its Roles carry the
  `actions.github.com/cleanup-protection` finalizer, which only the
  controller removes; take the scale set out first and let it go before
  touching `arc-controller`.

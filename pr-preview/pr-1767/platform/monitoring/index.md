# Monitoring

[kube-prometheus-stack](<https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack>): Prometheus, Grafana, Alertmanager, node-exporter and kube-state-metrics. node-exporter runs in its own `node-exporter` namespace so that only it is admitted at the `privileged` level ([security policies](<https://homelab.wlkr.ch/platform/security-policies/index.md#pod-security-admission>)). The chart's default rules are good; its default routing swallows every alert.

## At a glance

|  |  |
| --- | --- |
| Namespace | `monitoring` |
| Depends on | [Rook-Ceph](<https://homelab.wlkr.ch/platform/rook-ceph/index.md>) for Prometheus and Grafana volumes, [Authentik](<https://homelab.wlkr.ch/platform/authentik/index.md>) for the Grafana login |
| If it is down | No alerts and no metrics — and [Kured](<https://homelab.wlkr.ch/platform/kured/index.md>) loses the alert gate it checks before rebooting a node |
| Health check | `kubectl -n monitoring get prometheus,alertmanager`; the `Watchdog` alert should always be firing |
| UI | `monitoring.infra.k8s.wlkr.ch` (Grafana), `prometheus.` and `alertmanager.` |
| Files | `payload/platform/monitoring/`, `payload/platform/prometheus-operator-crds/` |

## Configuration

### CRDs

The `monitoring.coreos.com` CRDs are a separate `prometheus-operator-crds` Application, from the prometheus-community chart of that name, and this chart runs with `crds.enabled: false`. Other components render ServiceMonitors or PrometheusRules, and a missing kind fails their sync until the CRDs exist — see [Bootstrap convergence](<https://homelab.wlkr.ch/architecture/gitops/index.md#bootstrap-convergence>). The Application has no resources finalizer, so deleting it leaves the CRDs, and every ServiceMonitor, Prometheus and Alertmanager with them, in place.

> [!WARNING]
>
> **Two versions that have to agree**
>
> `prometheus-operator-crds` must carry the same operator version as kube-prometheus-stack; an operator newer than its CRDs can depend on fields they do not define. Renovate bumps them separately: merge the CRD bump first, or both together — see [Maintenance](<https://homelab.wlkr.ch/development/maintenance/index.md>).

### Alerting

Alertmanager routes to email. The chart's `config` is off (`alertmanagerSpec.useExistingSecret`) and `alertmanager-config.yaml` renders the whole `alertmanager.yaml` through ESO into `alertmanagerSpec.configSecret`, because only the password has a file form (`smtp_auth_password_file`) and the login and recipient are e-mail addresses that do not belong in Git.

| Setting in `alertmanager-config.yaml` | Why |
| --- | --- |
| `smtp_smarthost` on port 465 | Implicit TLS, which Alertmanager selects from the port; `smtp_require_tls` stays on so a move back to 587 cannot fall to plaintext |
| `smtp_from` equals the login | mailbox.org refuses a sender the login does not own |
| Grouping by `namespace`, with `group_interval` and `repeat_interval` in hours and a shorter pair for `critical`; resolved notifications sent | mailbox.org caps what an account may send. One mail per alert and namespace came to hundreds a day once a node was down and the account was refused. A namespace's alerts usually share a cause, so one mail lists them together |
| `Watchdog` routed to `null` | It fires continuously as proof the pipeline is alive; mailing it would train the recipient to filter it |
| `VeleroBackupInProgress` routed to `null` | It fires for every nightly backup as [Kured's gate](<https://homelab.wlkr.ch/platform/kured/index.md#configuration>), not as a problem |
| `KubeJobFailed` disabled in the chart (`defaultRules.disabled`) and redefined in `job-rules.yaml`, bound to Jobs started in the last day | The chart's rule fires for as long as a failed Job object exists, so one old failure reads as an outage for weeks. Failed Jobs carry no failure time, so the bound counts from the start |
| Inhibit rules repeated from the chart defaults | A supplied config replaces the whole document, so nothing is inherited |
| Every templated value through `toJson` | A password containing `:`, `#` or a quote cannot break the rendered file |
| `externalUrl` on both `prometheusSpec` and `alertmanagerSpec` in `application.yaml` | The "Source" and Alertmanager links in a mail are built from these; unset, they point at the in-cluster Service, which nothing outside can open |

Rules written here, beside the chart's:

| Alert | Fires when |
| --- | --- |
| `CertificateNotReady`, `CertificateExpiringSoon` | A Certificate is not Ready for 30 minutes, or has under 14 days left (renewal starts at 30) |
| `ArgoCDApplicationUnhealthy`, `ArgoCDApplicationOutOfSync` | An Application is Degraded or Missing for 15 minutes, or OutOfSync for an hour, longer than the [retries](<https://homelab.wlkr.ch/architecture/gitops/index.md#sync-policy>) run |
| `ExternalSecretNotReady`, `PushSecretNotReady` | One is not Ready for 15 minutes; after an OpenBao restart every one fires until it is unsealed |

The three values come from `kv/monitoring/smtp` (`username`, `password`, `to`); `make bao-secrets` prompts for them, or by hand:

```bash
bao kv put kv/monitoring/smtp username=... password=... to=...
```

For another provider, change `smtp_smarthost` in the template and the three values in OpenBao.

### Scrape targets

Every `ServiceMonitor` and `PodMonitor` in every namespace, through the five `*SelectorNilUsesHelmValues: false` settings: the chart treats an empty selector as unset and renders `release: kube-prometheus-stack` in its place, which limits Prometheus to its own targets. `ScrapeConfig` and `Probe` load from `monitoring` only, as a workload could aim them anywhere Prometheus reaches. `enforcedNamespaceLabel` stays off: several monitors rely on `honorLabels`.

`PrometheusRule`s load only from the namespaces the `apps` project cannot deploy to, matched on the `kubernetes.io/metadata.name` label the API server sets. [Kured](<https://homelab.wlkr.ch/platform/kured/index.md#configuration>) gates reboots on alert names, so a workload's rule named `CephHealthError` would otherwise stop OS updates cluster-wide. A rule in a new platform namespace loads only once that namespace is on the list in `ruleNamespaceSelector`.

| Target | Made scrapable by | Notes |
| --- | --- | --- |
| kube-scheduler, kube-controller-manager | A per-node `bind-address` patch in `ansible/templates/butane_node_config.yaml.j2` (kubeadm binds them to `127.0.0.1`) | HTTPS with a self-signed certificate, so the ServiceMonitors set `insecureSkipVerify`; the ServiceAccount token still authenticates; the control-plane monitors read the pod's projected token (`controlPlaneScrapeClass`), so no long-lived token Secret exists |
| etcd | A per-node `listen-metrics-urls` patch in the same template (kubeadm listens on `127.0.0.1` only) | Plain HTTP on 2381, serving only `/metrics` and `/health`; client and peer APIs stay on mTLS |
| Ceph | `monitoring.enabled` on the `CephCluster` — see [Rook-Ceph](<https://homelab.wlkr.ch/platform/rook-ceph/index.md#monitoring>) |  |
| kube-proxy | Not scraped (`kubeProxy.enabled: false`) | Cilium replaces it; the default would keep `KubeProxyDown` firing forever |
| CRD status fields: CloudNativePG's ObjectStores and Backups | kube-state-metrics' `customResourceState` config in the chart values, one `rbac.extraRules` entry per kind | A `StateSet` per enum field and a `Gauge` per timestamp; the rule that reads a kind sits with its component, in a namespace on the rule list above |
| Pod labels: OpenBao's `openbao-sealed` | kube-state-metrics' `metricLabelsAllowlist` in the chart values | Exported as `label_<name>` on `kube_pod_labels`; only listed labels, since every one adds a series per pod |

> [!NOTE]
>
> A cluster provisioned before the kubeadm change keeps the old flags in its static pod manifests; until those nodes are reprovisioned the three control-plane targets stay down.

### Grafana

| Setting | Why |
| --- | --- |
| `disable_login_form` and `oauth_auto_login` | The URL bounces straight to Authentik; `role_attribute_path` with `role_attribute_strict` maps the three Grafana groups and refuses everyone else, see [Groups and roles](<https://homelab.wlkr.ch/platform/authentik/index.md#groups-and-roles>) |
| `analytics.*` off and `disable_gravatar` | Grafana otherwise reports usage to Grafana Labs, asks grafana.com for updates and fetches avatars from Gravatar; the network policy names none of those. Telemetry is off in every component here |
| `grafana-oidc.yaml` | The OIDC client is generated into OpenBao and read by both sides — see [Authentik](<https://homelab.wlkr.ch/platform/authentik/index.md#client-secrets>) |
| `grafana.route.main` | The chart renders Grafana's `HTTPRoute`. Prometheus and Alertmanager stay in `httproute.yaml`: their routes point at Authentik's outpost, which no chart template can express |
| `grafana.admin.existingSecret`, from `grafana-admin.yaml` | Left unset, the chart generates a new password on every render, so the Secret is always OutOfSync and Grafana restarts on every sync. The admin account is the [break-glass path](<https://homelab.wlkr.ch/platform/authentik/index.md#when-authentik-is-down>); never set `grafana.adminPassword` in Git |
| `grafana.rbac.create: false` with `grafana-sidecar-rbac.yaml`, and `sidecar.*.resource: configmap` | Dashboards and datasources are ConfigMaps, so a ClusterRole bound by a RoleBinding in each `searchNamespace` namespace grants those alone, not the chart's cluster-wide ConfigMaps-and-Secrets pair; the chart's binding is dropped rather than repointed because a `roleRef` is immutable |
| `Recreate` strategy | The dashboard PVC is RWO; under `RollingUpdate` the new pod waits for a volume the old pod releases only once the new one is Ready |
| Prometheus and Grafana memory limits well above their requests | Both grow with series count and dashboard load; the limit is a ceiling for a runaway, sized by the [2.5x-peak rule](<https://homelab.wlkr.ch/platform/index.md#components>), not a budget |
| Prometheus's memory request at its measured median working set | A request below real use lets a drain move it onto an 8 GB worker that swaps until the kubelet goes `NotReady`. 2.5x its peak exceeds every node, so the limit stays under the largest node's allocatable |
| Prometheus `retention` and `retentionSize` | Whichever is hit first wins; the size cap drops the oldest blocks instead of filling the disk and crashlooping Prometheus |
| Alertmanager `automountServiceAccountToken: false`, on the ServiceAccount and in `alertmanagerSpec` | Nothing binds a role to it — the operator talks to the API — so the token would only be a credential in a pod reachable from the Gateway. The operator sets the pod field itself, so the account's setting alone does nothing |

### Dashboards

Where a chart can render a dashboard, it does; only charts without one get a vendored copy, which Renovate does not update — the header of each file names its upstream tag.

| Component | Dashboards | Source |
| --- | --- | --- |
| [Cilium](<https://homelab.wlkr.ch/platform/cilium/index.md>) | Cilium Metrics, Cilium Operator, four Hubble dashboards | Chart: `dashboards`, `operator.dashboards`, `hubble.metrics.dashboards` |
| [OpenBao](<https://homelab.wlkr.ch/platform/openbao/index.md>) | OpenBao | Chart: `serverTelemetry.grafanaDashboard` |
| [External Secrets](<https://homelab.wlkr.ch/platform/external-secrets/index.md>) | External Secrets Operator | Chart: `grafanaDashboard` |
| [Rook-Ceph](<https://homelab.wlkr.ch/platform/rook-ceph/index.md>) | Ceph Cluster, Ceph - OSD (Single), Ceph - Pools | Vendored: `rook-ceph/grafana-dashboards.yaml` |
| [Alloy](<https://homelab.wlkr.ch/platform/logging/index.md>) | Alloy / Controller, Alloy / Loki Components, Alloy / Resources | Vendored: `logging/grafana-dashboards.yaml` |
| [Loki](<https://homelab.wlkr.ch/platform/logging/index.md>) | The Loki mixin: Reads, Writes, Operational, Chunks, Retention and the rest | Chart: `monitoring.dashboards`, with `monitoring.rules` for the recording rules they read |
| ArgoCD | ArgoCD | Vendored: `argocd-config/grafana-dashboards.yaml` |
| [Tetragon](<https://homelab.wlkr.ch/platform/tetragon/index.md>) | Tetragon | Written for this cluster: `tetragon/grafana-dashboards.yaml`; upstream ships none |
| [GitHub runners](<https://homelab.wlkr.ch/platform/github-runners/index.md>) | GitHub Actions Runners | Written for this cluster: `arc-controller/grafana-dashboards.yaml`; upstream's sample filters on labels ARC 0.15 does not export |
| [Trivy Operator](<https://homelab.wlkr.ch/platform/trivy-operator/index.md>) | Trivy Operator | Written for this cluster: `trivy-operator/grafana-dashboards.yaml` |

## Usage

### Adding a dashboard

Add a ConfigMap labelled `grafana_dashboard: "1"` next to the component it describes, in a file named `grafana-dashboards.yaml` and a ConfigMap named `<component>-grafana-dashboards`; a Secret carrying the label is not read. The sidecar watches only the platform namespaces listed in `sidecar.dashboards.searchNamespace`, so a workload cannot replace a dashboard; a component in a new namespace adds it there and a RoleBinding in `grafana-sidecar-rbac.yaml`, and the `apps` project must not be able to deploy to it. Prefer the chart's own dashboard where it has one. A dashboard built in the UI survives restarts on the PVC but lives nowhere else: not in Git, not on a rebuilt cluster.

### Growing the Prometheus volume

Kubernetes cannot expand a StatefulSet's `volumeClaimTemplate`, so raising the request in `payload/platform/monitoring/application.yaml` does not resize the claim that already exists. The operator handles its half: it rebuilds the StatefulSet orphaned so the pod survives, logging `recreating StatefulSet because the update operation wasn't possible`. Only the claim is left behind.

1. Merge the size bump and wait for the Application to sync.

   ```bash
   kubectl -n argocd get app kube-prometheus-stack -o jsonpath='{.status.sync.status}'
   ```
2. Patch the claim to the same size. This is the one step nothing automates.

   ```bash
   SIZE=$(awk '/prometheusSpec:/{f=1} f&&/storage: /{print $2; exit}' \
     payload/platform/monitoring/application.yaml)
   kubectl -n monitoring patch pvc \
     prometheus-kube-prometheus-stack-prometheus-db-prometheus-kube-prometheus-stack-prometheus-0 \
     --patch "{\"spec\": {\"resources\": {\"requests\": {\"storage\": \"$SIZE\"}}}}"
   ```
3. The claim parks on `FileSystemResizePending` and the filesystem grows at the next mount, so the pod restarts and replays its WAL: a scrape gap of about a minute. Wait for the claim to report the new capacity.

   ```bash
   kubectl -n monitoring get pvc -w \
     -o custom-columns=NAME:.metadata.name,CAP:.status.capacity.storage
   ```

If the operator has not rebuilt the StatefulSet within a sync interval, delete it with `--cascade=orphan`, which keeps the pod and the claim.

```bash
kubectl -n monitoring delete statefulset \
  -l operator.prometheus.io/name=kube-prometheus-stack-prometheus --cascade=orphan
```

Alertmanager and Grafana take the same two steps, with their own claim names.

### Resetting the Grafana admin password

Grafana reads the password only when it creates its database, so changing `kv/monitoring/grafana-admin` later does not change what it checks. Reset it to the value in OpenBao, not a new one: `grafana-admin.yaml` pushes the password with `updatePolicy: IfNotExists`, so OpenBao keeps the first value and a rebuilt Grafana starts from it.

```bash
PASSWORD=$(kubectl -n openbao exec openbao-0 -- \
  bao kv get -mount=kv -field=password monitoring/grafana-admin)
kubectl -n monitoring exec deploy/kube-prometheus-stack-grafana -c grafana -- \
  grafana cli admin reset-admin-password "$PASSWORD"
```

## Health check

```bash
kubectl -n monitoring get prometheus,alertmanager
kubectl -n monitoring get prometheus kube-prometheus-stack-prometheus \
  -o jsonpath='{.spec.serviceMonitorSelector}'   # should print {}
kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093:9093
# then open http://localhost:9093 and check Status -> Config
```

`Watchdog` proves the pipeline only as far as Alertmanager; SMTP auth and an expired password are unmonitored. Send yourself a test alert once.

## Pitfalls

> [!WARNING]
>
> **Prometheus is a single replica, Alertmanager is not**
>
> Alertmanager runs two replicas spread over the workers with a PDB that lets one go, so a [Kured](<https://homelab.wlkr.ch/platform/kured/index.md>) drain keeps alerting up. A second Prometheus would double the memory limit and the volume in `application.yaml`, which the [2.5x-peak rule](<https://homelab.wlkr.ch/platform/index.md#components>) leaves no room for on a worker, so it stays single: its node's drain loses metrics for the restart, and Kured's alert gate fails closed until it is back.

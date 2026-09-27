---
description: "The descheduler evicts pods that a drain or reboot left piled on the surviving nodes, so the scheduler can spread them out again."
---

# Descheduler

The scheduler places a pod once and never looks at it again. After
[Kured](kured.md) drains a node, everything that lived there restarts on the
others and stays there when the node comes back, so a cluster that has
rebooted through an update ends up with one idle node and the rest loaded.
The [descheduler](https://github.com/kubernetes-sigs/descheduler) runs on a
schedule, evicts a few pods from the loaded nodes, and lets the scheduler
place them where there is room.

## At a glance

| | |
| --- | --- |
| Namespace | `descheduler` |
| Depends on | [metrics-server](metrics-server.md) for the node usage `LowNodeUtilization` compares |
| If it is down | Nothing visible: pods stay where the last drain left them |
| Health check | `kubectl -n descheduler get jobs` &rarr; the last run `Complete` |
| Files | `payload/platform/descheduler/application.yaml` |

## Configuration

The values are in `payload/platform/descheduler/application.yaml`; the
reasoning:

| Setting | Why |
| --- | --- |
| `kind: CronJob` on a two-hour schedule | A Deployment would poll continuously for a cluster that changes shape once per Flatcar update. A pass every two hours drifts a rebooted cluster back into balance the same day |
| `nodeFit: true` | A pod is only evicted when another node has room for it, so an eviction never turns into a Pending pod |
| Local-storage and PVC pods at the defaults | `emptyDir` pods are protected because their data goes with them; PVC pods are not, because Ceph volumes follow the pod to any node |
| `maxNoOfPodsToEvictPerNode` and `evictionLimits.node` | A few pods per node per pass: one Kured reboot already moved everything on a node once, a second mass move is what this component is meant to avoid |
| `LowNodeUtilization` with `metricsUtilization.source: KubernetesMetrics` | Usage comes from metrics-server, not from requests. Requests here are still being tuned and many are far below what the pod uses, so a requests-based view would call a loaded node idle |
| `thresholds` and `targetThresholds` | A node under the low mark is a candidate to receive, one over the high mark a candidate to lose pods; nodes in between are left alone |
| `RemoveDuplicates` and `RemovePodsViolatingTopologySpreadConstraint` for both `DoNotSchedule` and `ScheduleAnyway` | After a drain, replicas of one workload sit on the same node; both plugins put them back on separate ones. Soft constraints count too, because the scheduler honoured them only until the drain |
| `RemovePodsHavingTooManyRestarts` | A pod in a restart loop for hours is more likely stuck on its node than broken; a move is cheap and the restart count resets |
| Metrics off | A CronJob pod exists for seconds; a `ServiceMonitor` would scrape an empty Service |

## Usage

Nothing to operate. To watch a pass:

```bash
kubectl -n descheduler create job --from=cronjob/descheduler manual-$(date +%s)
kubectl -n descheduler logs -l app.kubernetes.io/name=descheduler --tail=100
```

Every eviction is an event on the pod, so `kubectl get events -A
--field-selector reason=Descheduled` shows what moved and why.

## Health check

```bash
kubectl -n descheduler get cronjob,jobs
kubectl -n descheduler logs -l app.kubernetes.io/name=descheduler --tail=100 | grep -i "evict\|error"
kubectl top nodes    # what LowNodeUtilization sees
```

## Pitfalls

- **It only evicts, never schedules.** Where a pod lands is the scheduler's
  decision; the descheduler assumes the scheduler will do better the second
  time, which holds after a drain and not much else.
- **It honours PodDisruptionBudgets.** An eviction that would violate a PDB
  is refused, so a single-instance [CloudNativePG](cloudnative-pg.md) cluster
  with a PDB is never moved — the same PDB that blocks a drain blocks the
  descheduler.
- **One pod per over-utilized node per pass** when usage comes from
  metrics-server. A badly skewed cluster takes several passes, which is the
  point of running it every two hours rather than once.
- **A move is a restart.** Anything without a second replica is briefly down
  when it is evicted; `nodeFit` guarantees a place to go, not zero downtime.

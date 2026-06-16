# Sensing Hospital on Kubernetes (EKS target)

Kustomize base that lifts the local `docker compose --profile mock-app` mesh
into Kubernetes. The app containers are unchanged — they're already stateless,
12-factor, single-process, and expose `/healthz`, so each compose service maps
1:1 to a Deployment + Service here.

## What's here

| File | Contents |
|---|---|
| `base/namespace.yaml` | `sensing-hospital` namespace |
| `base/configmap.yaml` | shared non-secret env (DD_ENV, DD_SITE, service URLs) |
| `base/secret.example.yaml` | template Secret for DD_API_KEY / RUM creds (do **not** commit real values) |
| `base/datastores.yaml` | Postgres + Redis Deployments/Services/PVC for a self-contained cluster |
| `base/app.yaml` | all 12 app Deployments + Services |
| `base/kustomization.yaml` | ties it together |

## Deploy

```bash
kubectl apply -k k8s/sensing-hospital/base
```

## Two things you swap for real AWS

1. **Datadog Agent** — do NOT hand-roll it. Install the **Datadog Operator**
   (or the `datadog` Helm chart) with APM, DogStatsD, logs, process, and the
   cluster agent enabled. Pods send traces to the node agent via
   `DD_AGENT_HOST` (already wired from `status.hostIP`); DogStatsD uses the
   same host. The Postgres DBM + Redis checks move to the Operator's
   autodiscovery annotations (the `conf.d` in `docker/datadog/sensing/` is the
   reference).
2. **Data stores** — `datastores.yaml` runs Postgres + Redis in-cluster so the
   base is self-contained. In AWS, delete it and point `DB_HOST` / `REDIS_HOST`
   in the ConfigMap at **RDS** and **ElastiCache** instead. Nothing in the app
   images changes.

## Notes

- Image: every app Deployment uses `sensing-hospital:latest`. Build from
  `docker/sensing_hospital/Dockerfile` and push to ECR, then set the image via
  a Kustomize overlay (`images:` transformer).
- Service DNS names match the compose service names (`care-event-router`,
  `sensing-postgres`, …) so the in-code defaults resolve unchanged.
- This is the lift target, not yet exercised on a live cluster — `kubectl
  apply --dry-run=client -k` validates structure; a real apply needs the image
  in a registry and the Datadog Operator installed.

# Kubernetes Deployment Manifests

This directory contains Kubernetes manifests for deploying Prism to a VPS running k3s.

## Quick Start

See [DEPLOYMENT.md](./DEPLOYMENT.md) for the complete deployment guide.

## Structure

```
k8s/
├── DEPLOYMENT.md                 # Complete deployment guide
├── README.md                     # This file
├── namespace.yaml                # Prism namespace
├── configmap.yaml                # Non-sensitive configuration + init.sql
├── secret.yaml.template          # Template for secrets (DO NOT commit real values)
│
├── postgres/                     # PostgreSQL database
│   ├── pvc.yaml                 # 10Gi persistent volume
│   ├── statefulset.yaml         # Single-replica StatefulSet
│   └── service.yaml             # ClusterIP (internal only)
│
├── redis/                        # Redis event bus + cache
│   ├── pvc.yaml                 # 2Gi persistent volume
│   ├── statefulset.yaml         # Single-replica StatefulSet with AOF persistence
│   └── service.yaml             # ClusterIP (internal only)
│
├── chatbot-api/                  # FastAPI chatbot service
│   ├── deployment.yaml          # 1 replica, health checks
│   └── service.yaml             # NodePort 30100 (exposed via Caddy)
│
├── ingestion-api/                # FastAPI ingestion service
│   ├── deployment.yaml          # 1 replica, PII redaction
│   └── service.yaml             # NodePort 30101 (exposed via Caddy)
│
├── chatbot-ui/                   # Next.js frontend
│   ├── deployment.yaml          # 1 replica, production build
│   └── service.yaml             # NodePort 30001 (exposed via Caddy)
│
├── workers/                      # Background workers
│   ├── log-writer-deployment.yaml           # Batched log writer
│   ├── metrics-roller-deployment.yaml       # 60-second rollup aggregator
│   ├── metrics-reconciler-deployment.yaml   # Reconciler (5-min loop)
│   └── partition-cron-deployment.yaml       # Partition manager (24h loop)
│
└── scripts/
    └── apply-all.sh              # Helper script to deploy all manifests
```

## Services Exposed

| Service | Type | Port | NodePort | Exposed to Caddy? |
|---------|------|------|----------|-------------------|
| chatbot-ui | NodePort | 3000 | 30001 | Yes (app.domain.com) |
| chatbot-api | NodePort | 8000 | 30100 | Yes (api.domain.com) |
| ingestion-api | NodePort | 8001 | 30101 | Yes (ingest.domain.com) |
| postgres | ClusterIP | 5432 | - | No (internal only) |
| redis | ClusterIP | 6379 | - | No (internal only) |

## Container Images

All images are built from the root of the Prism repository:

- `IMAGE_REGISTRY/prism-chatbot-api:latest` ← `services/chatbot-api/Dockerfile`
- `IMAGE_REGISTRY/prism-ingestion-api:latest` ← `services/ingestion-api/Dockerfile`
- `IMAGE_REGISTRY/prism-workers:latest` ← `services/workers/Dockerfile` (shared by all 4 workers)
- `IMAGE_REGISTRY/prism-chatbot-ui:latest` ← `web/Dockerfile` (multi-stage production build)

## Environment Variables

### Sensitive (in Secret)
- `POSTGRES_PASSWORD`
- `REDIS_PASSWORD`
- `PRISM_CREDS_KEY` (Fernet key for credential encryption)
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`
- `BEDROCK_AWS_ACCESS_KEY_ID`, `BEDROCK_AWS_SECRET_ACCESS_KEY`, `BEDROCK_AWS_REGION`

### Non-sensitive (in ConfigMap)
- `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_HOST`
- `REDIS_HOST`
- `INGESTION_URL`, `CHATBOT_API_URL`
- `PRISM_LOG_LEVEL`, `PRISM_KEEP_RAW`, `PRISM_REDACTOR`
- `PARTITION_RETENTION_DAYS`

## Deployment Workflow

1. **Build images** with your registry path
2. **Push images** to registry
3. **Create secret** on VPS from `/root/prism-secrets.env`
4. **Update manifests** to use your image registry
5. **Apply manifests** using `./scripts/apply-all.sh` or manually
6. **Configure Caddy** to reverse-proxy to NodePorts
7. **Verify** health endpoints

## Important Notes

- **NEXT_PUBLIC_CHATBOT_API_URL** is baked into the frontend bundle at **build time**. If your domain changes, rebuild the image.
- **Workers share one image** (`prism-workers:latest`). The `command` field selects which worker runs.
- **metrics-reconciler and partition-cron** are Deployments (not CronJobs) because they implement internal scheduling loops.
- **Never commit `k8s/secret.yaml`** with real values. Use the template only.

## Caddy Configuration

Add to `/etc/caddy/Caddyfile`:

```caddyfile
app.yourdomain.com {
    reverse_proxy localhost:30001
}

api.yourdomain.com {
    reverse_proxy localhost:30100
}

ingest.yourdomain.com {
    reverse_proxy localhost:30101
}
```

Then reload: `sudo systemctl reload caddy`

## Troubleshooting

See [DEPLOYMENT.md](./DEPLOYMENT.md) for detailed troubleshooting steps.

Common issues:
- Pod not starting → `kubectl -n prism describe pod <name>`
- Image pull errors → Check `imagePullSecrets` and registry credentials
- Connection errors → Verify service DNS and health checks
- NodePort not accessible → Check iptables rules and Caddy config

## References

- [DEPLOYMENT.md](./DEPLOYMENT.md) - Complete deployment guide
- [../docs/architecture.md](../docs/architecture.md) - System architecture
- [../docs/runbook.md](../docs/runbook.md) - Local dev runbook

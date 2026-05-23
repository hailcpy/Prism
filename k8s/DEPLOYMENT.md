# Prism Kubernetes Deployment Guide

This guide covers deploying Prism to a VPS running k3s with Caddy as the reverse proxy.

## Architecture Overview

- **9 services** running in Kubernetes (k3s)
- **Caddy** on the host handles TLS termination and reverse proxying
- **NodePort** services expose APIs and UI to Caddy on localhost
- **ClusterIP** services keep Postgres and Redis internal

## Prerequisites

- VPS with root access
- Domain name with DNS pointing to the VPS
- Caddy already installed and configured on the host

## Directory Structure

```
k8s/
├── namespace.yaml
├── configmap.yaml
├── secret.yaml.template          # Template only, DO NOT commit real secrets
├── postgres/
│   ├── pvc.yaml
│   ├── statefulset.yaml
│   └── service.yaml
├── redis/
│   ├── pvc.yaml
│   ├── statefulset.yaml
│   └── service.yaml
├── chatbot-api/
│   ├── deployment.yaml
│   └── service.yaml
├── ingestion-api/
│   ├── deployment.yaml
│   └── service.yaml
├── chatbot-ui/
│   ├── deployment.yaml
│   └── service.yaml
├── workers/
│   ├── log-writer-deployment.yaml
│   ├── metrics-roller-deployment.yaml
│   ├── metrics-reconciler-deployment.yaml
│   └── partition-cron-deployment.yaml
└── scripts/
    └── apply-all.sh
```

---

## Phase 0: One-Time VPS Setup

### 1. Install k3s

```bash
# Install k3s with Traefik disabled (Caddy handles ingress)
curl -sfL https://get.k3s.io | sh -s - --disable traefik

# Verify k3s is running
sudo k3s kubectl get nodes

# Set up kubeconfig for non-root user
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $USER ~/.kube/config
export KUBECONFIG=~/.kube/config

# Add to your shell profile
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc

# Verify kubectl works
kubectl get nodes
```

### 2. Optional: Restrict NodePort Access

By default, k3s exposes NodePorts on all interfaces. Lock them down to localhost only:

```bash
# Block external access to NodePort range
sudo iptables -A INPUT -p tcp --dport 30000:32767 -j DROP

# Allow localhost
sudo iptables -I INPUT -p tcp -s 127.0.0.1 --dport 30000:32767 -j ACCEPT

# Make persistent (Debian/Ubuntu)
sudo apt-get install iptables-persistent
sudo netfilter-persistent save
```

---

## Phase 1: Container Registry Setup

Choose a container registry and authenticate:

### Option A: GitHub Container Registry (ghcr.io) - Recommended

```bash
# Create a Personal Access Token (PAT) with `write:packages` scope at:
# https://github.com/settings/tokens

# Login
echo $GITHUB_PAT | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin

# Set registry variable
export IMAGE_REGISTRY=ghcr.io/YOUR_GITHUB_USERNAME
```

### Option B: Docker Hub

```bash
docker login

export IMAGE_REGISTRY=docker.io/YOUR_DOCKERHUB_USERNAME
```

### Update Manifests with Registry Path

Replace `IMAGE_REGISTRY` in all deployment YAML files:

```bash
find k8s/ -name "*.yaml" -exec sed -i "s|IMAGE_REGISTRY|$IMAGE_REGISTRY|g" {} \;
```

Or manually edit each deployment to use your registry path.

---

## Phase 2: Build and Push Container Images

### 1. Build Images

```bash
cd /path/to/Prism

# Important: Set your public API URL for the frontend build
export NEXT_PUBLIC_CHATBOT_API_URL=https://api.yourdomain.com

# Build all images
docker build -t $IMAGE_REGISTRY/prism-chatbot-api:latest \
  -f services/chatbot-api/Dockerfile .

docker build -t $IMAGE_REGISTRY/prism-ingestion-api:latest \
  -f services/ingestion-api/Dockerfile .

docker build -t $IMAGE_REGISTRY/prism-workers:latest \
  -f services/workers/Dockerfile .

# Frontend requires NEXT_PUBLIC_CHATBOT_API_URL baked in at build time
docker build -t $IMAGE_REGISTRY/prism-chatbot-ui:latest \
  --build-arg NEXT_PUBLIC_CHATBOT_API_URL=$NEXT_PUBLIC_CHATBOT_API_URL \
  ./web
```

### 2. Push Images

```bash
docker push $IMAGE_REGISTRY/prism-chatbot-api:latest
docker push $IMAGE_REGISTRY/prism-ingestion-api:latest
docker push $IMAGE_REGISTRY/prism-workers:latest
docker push $IMAGE_REGISTRY/prism-chatbot-ui:latest
```

### 3. (Optional) Create Image Pull Secret for Private Registry

If using a private registry, create an image pull secret:

```bash
kubectl create secret docker-registry regcred \
  --namespace prism \
  --docker-server=ghcr.io \
  --docker-username=YOUR_GITHUB_USERNAME \
  --docker-password=$GITHUB_PAT
```

Then add to each Deployment spec:
```yaml
spec:
  template:
    spec:
      imagePullSecrets:
      - name: regcred
```

---

## Phase 3: Generate Secrets

Create secrets file on the VPS:

```bash
# Generate secure passwords and keys
cat > /root/prism-secrets.env << 'EOF'
POSTGRES_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
REDIS_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
PRISM_CREDS_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...
BEDROCK_AWS_ACCESS_KEY_ID=AKIA...
BEDROCK_AWS_SECRET_ACCESS_KEY=...
BEDROCK_AWS_REGION=us-west-2
EOF

# Secure the file
chmod 600 /root/prism-secrets.env

# Edit and fill in your actual API keys
nano /root/prism-secrets.env
```

---

## Phase 4: Deploy to Kubernetes

### Option 1: Use the Helper Script

```bash
cd /path/to/Prism

# Apply namespace and config first
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml

# Create secret
kubectl create secret generic prism-secret \
  --namespace prism \
  --from-env-file=/root/prism-secrets.env

# Deploy all services
./k8s/scripts/apply-all.sh
```

### Option 2: Manual Step-by-Step

```bash
# 1. Namespace and Config
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml

# 2. Create Secret
kubectl create secret generic prism-secret \
  --namespace prism \
  --from-env-file=/root/prism-secrets.env

# 3. Deploy Postgres
kubectl apply -f k8s/postgres/pvc.yaml
kubectl apply -f k8s/postgres/statefulset.yaml
kubectl apply -f k8s/postgres/service.yaml

# Wait for Postgres
kubectl -n prism rollout status statefulset/postgres --timeout=120s

# 4. Deploy Redis
kubectl apply -f k8s/redis/pvc.yaml
kubectl apply -f k8s/redis/statefulset.yaml
kubectl apply -f k8s/redis/service.yaml

# Wait for Redis
kubectl -n prism rollout status statefulset/redis --timeout=60s

# 5. Deploy APIs
kubectl apply -f k8s/chatbot-api/deployment.yaml
kubectl apply -f k8s/chatbot-api/service.yaml
kubectl apply -f k8s/ingestion-api/deployment.yaml
kubectl apply -f k8s/ingestion-api/service.yaml

# 6. Deploy Workers
kubectl apply -f k8s/workers/log-writer-deployment.yaml
kubectl apply -f k8s/workers/metrics-roller-deployment.yaml
kubectl apply -f k8s/workers/metrics-reconciler-deployment.yaml
kubectl apply -f k8s/workers/partition-cron-deployment.yaml

# 7. Deploy UI
kubectl apply -f k8s/chatbot-ui/deployment.yaml
kubectl apply -f k8s/chatbot-ui/service.yaml

# 8. Verify
kubectl -n prism get pods
kubectl -n prism get services
```

---

## Phase 5: Configure Caddy

Add these blocks to your Caddyfile:

```caddyfile
# Chatbot UI
app.yourdomain.com {
    reverse_proxy localhost:30001
}

# Chatbot API (public-facing for browser calls)
api.yourdomain.com {
    reverse_proxy localhost:30100
}

# Ingestion API (for SDK clients)
ingest.yourdomain.com {
    reverse_proxy localhost:30101
}
```

Reload Caddy:

```bash
sudo systemctl reload caddy

# Or if using Caddy directly
sudo caddy reload --config /etc/caddy/Caddyfile
```

---

## Phase 6: Smoke Test

```bash
# Test API health
curl https://api.yourdomain.com/healthz

# Expected: {"status":"ok"}

# Test ingestion API
curl https://ingest.yourdomain.com/healthz

# Test UI
curl -I https://app.yourdomain.com/

# Test internal cluster DNS
kubectl -n prism run test --rm -it --image=curlimages/curl -- \
  curl http://chatbot-api.prism.svc.cluster.local:8000/healthz
```

---

## Phase 7: Verify Deployment

```bash
# Check all pods
kubectl -n prism get pods

# Check services and NodePorts
kubectl -n prism get services

# Expected output includes:
# chatbot-ui    NodePort   <cluster-ip>   <none>   3000:30001/TCP
# chatbot-api   NodePort   <cluster-ip>   <none>   8000:30100/TCP
# ingestion-api NodePort   <cluster-ip>   <none>   8001:30101/TCP
# postgres      ClusterIP  <cluster-ip>   <none>   5432/TCP
# redis         ClusterIP  <cluster-ip>   <none>   6379/TCP

# Check logs
kubectl -n prism logs -l app=chatbot-api --tail=50
kubectl -n prism logs -l app=log-writer --tail=50
```

---

## Updating Deployments (Rolling Updates)

### 1. Rebuild and Push New Image

```bash
# Example: update chatbot-api
docker build -t $IMAGE_REGISTRY/prism-chatbot-api:latest \
  -f services/chatbot-api/Dockerfile .

docker push $IMAGE_REGISTRY/prism-chatbot-api:latest
```

### 2. Trigger Rolling Restart

Since we use the `:latest` tag with `imagePullPolicy: Always`, trigger a restart:

```bash
kubectl -n prism rollout restart deployment/chatbot-api

# Monitor rollout
kubectl -n prism rollout status deployment/chatbot-api
```

### 3. Rollback if Needed

```bash
kubectl -n prism rollout undo deployment/chatbot-api
```

---

## Troubleshooting

### Pod Not Starting

```bash
# Describe pod to see events
kubectl -n prism describe pod <pod-name>

# Check logs
kubectl -n prism logs <pod-name>

# Check previous logs (if pod restarted)
kubectl -n prism logs <pod-name> --previous
```

### Database Connection Issues

```bash
# Verify Postgres is running
kubectl -n prism get pods -l app=postgres

# Check Postgres logs
kubectl -n prism logs -l app=postgres

# Test connection from a pod
kubectl -n prism run psql-test --rm -it --image=postgres:16-alpine -- \
  psql postgresql://prism:PASSWORD@postgres.prism.svc.cluster.local:5432/prism
```

### Redis Connection Issues

```bash
# Check Redis logs
kubectl -n prism logs -l app=redis

# Test connection
kubectl -n prism run redis-test --rm -it --image=redis:7-alpine -- \
  redis-cli -h redis.prism.svc.cluster.local -a PASSWORD ping
```

### NodePort Not Accessible

```bash
# Check if service exists
kubectl -n prism get svc chatbot-api

# Verify NodePort is open
sudo netstat -tlnp | grep :30100

# Test from localhost
curl http://localhost:30100/healthz
```

### Image Pull Failures

```bash
# Check if imagePullSecret is configured
kubectl -n prism get deployment chatbot-api -o yaml | grep imagePullSecrets

# Verify secret exists
kubectl -n prism get secret regcred

# Check pod events
kubectl -n prism describe pod <pod-name>
```

---

## Monitoring

### View All Pods

```bash
kubectl -n prism get pods -o wide
```

### View Logs

```bash
# Tail logs for a specific deployment
kubectl -n prism logs -f deployment/chatbot-api

# View logs for all workers
kubectl -n prism logs -l app=log-writer --tail=100
kubectl -n prism logs -l app=metrics-roller --tail=100
```

### Resource Usage

```bash
# Top pods
kubectl -n prism top pods

# Top nodes
kubectl top nodes
```

---

## Scaling

### Scale a Deployment

```bash
# Scale chatbot-api to 2 replicas
kubectl -n prism scale deployment/chatbot-api --replicas=2

# Verify
kubectl -n prism get pods -l app=chatbot-api
```

**Note:** Do NOT scale StatefulSets (postgres, redis) without proper setup for clustering.

---

## Backup and Restore

### Backup Postgres

```bash
# From inside the postgres pod
kubectl -n prism exec -it postgres-0 -- \
  pg_dump -U prism prism > prism-backup-$(date +%Y%m%d).sql

# Or from host with port-forward
kubectl -n prism port-forward svc/postgres 5432:5432 &
pg_dump -h localhost -U prism prism > prism-backup-$(date +%Y%m%d).sql
```

### Restore Postgres

```bash
kubectl -n prism exec -i postgres-0 -- \
  psql -U prism prism < prism-backup.sql
```

---

## Complete Teardown

```bash
# Delete all resources in namespace
kubectl delete namespace prism

# Delete PVCs (if not auto-deleted)
kubectl -n prism delete pvc --all

# Uninstall k3s (if needed)
sudo /usr/local/bin/k3s-uninstall.sh
```

---

## Important Notes

### NEXT_PUBLIC_CHATBOT_API_URL

This environment variable is **baked into the frontend bundle at build time**. If your domain changes, you must:

1. Rebuild the image with the new URL:
   ```bash
   docker build -t $IMAGE_REGISTRY/prism-chatbot-ui:latest \
     --build-arg NEXT_PUBLIC_CHATBOT_API_URL=https://api.newdomain.com \
     ./web
   ```

2. Push and restart:
   ```bash
   docker push $IMAGE_REGISTRY/prism-chatbot-ui:latest
   kubectl -n prism rollout restart deployment/chatbot-ui
   ```

### Shared Worker Image

All four worker deployments (log-writer, metrics-roller, metrics-reconciler, partition-cron) use the same Docker image (`prism-workers:latest`). The `command` field in each deployment selects which worker module to run.

### Workers are Deployments, Not CronJobs

The `metrics-reconciler` and `partition-cron` services implement their own internal scheduling loops (5-minute and 24-hour intervals respectively). They are deployed as `Deployment` resources with `replicas: 1`, not as `CronJob` resources.

### Secrets Management

Secrets are created from `/root/prism-secrets.env` on the VPS and never committed to git. The `k8s/secret.yaml.template` file is a template for documentation only.

---

## References

- [k3s Documentation](https://docs.k3s.io/)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Caddy Documentation](https://caddyserver.com/docs/)
- Prism Architecture: `docs/architecture.md`
- Prism Runbook: `docs/runbook.md`

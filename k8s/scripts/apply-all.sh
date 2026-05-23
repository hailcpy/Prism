#!/bin/bash
set -e

echo "=== Applying Prism K8s Manifests ==="
echo

echo "1. Creating namespace..."
kubectl apply -f k8s/namespace.yaml

echo "2. Creating ConfigMaps..."
kubectl apply -f k8s/configmap.yaml

echo "3. Deploying Postgres..."
kubectl apply -f k8s/postgres/pvc.yaml
kubectl apply -f k8s/postgres/statefulset.yaml
kubectl apply -f k8s/postgres/service.yaml

echo "Waiting for Postgres to be ready..."
kubectl -n prism rollout status statefulset/postgres --timeout=120s

echo "4. Deploying Redis..."
kubectl apply -f k8s/redis/pvc.yaml
kubectl apply -f k8s/redis/statefulset.yaml
kubectl apply -f k8s/redis/service.yaml

echo "Waiting for Redis to be ready..."
kubectl -n prism rollout status statefulset/redis --timeout=60s

echo "5. Deploying Chatbot API..."
kubectl apply -f k8s/chatbot-api/deployment.yaml
kubectl apply -f k8s/chatbot-api/service.yaml

echo "6. Deploying Ingestion API..."
kubectl apply -f k8s/ingestion-api/deployment.yaml
kubectl apply -f k8s/ingestion-api/service.yaml

echo "7. Deploying Workers..."
kubectl apply -f k8s/workers/log-writer-deployment.yaml
kubectl apply -f k8s/workers/metrics-roller-deployment.yaml
kubectl apply -f k8s/workers/metrics-reconciler-deployment.yaml
kubectl apply -f k8s/workers/partition-cron-deployment.yaml

echo "8. Deploying Chatbot UI..."
kubectl apply -f k8s/chatbot-ui/deployment.yaml
kubectl apply -f k8s/chatbot-ui/service.yaml

echo
echo "=== Waiting for all deployments ==="
kubectl -n prism rollout status deployment/chatbot-api --timeout=120s
kubectl -n prism rollout status deployment/ingestion-api --timeout=120s
kubectl -n prism rollout status deployment/chatbot-ui --timeout=120s
kubectl -n prism rollout status deployment/log-writer --timeout=60s
kubectl -n prism rollout status deployment/metrics-roller --timeout=60s
kubectl -n prism rollout status deployment/metrics-reconciler --timeout=60s
kubectl -n prism rollout status deployment/partition-cron --timeout=60s

echo
echo "=== Deployment Complete ==="
echo "All pods:"
kubectl -n prism get pods
echo
echo "All services:"
kubectl -n prism get services

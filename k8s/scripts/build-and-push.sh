#!/bin/bash
set -e

# Check required env vars
if [ -z "$IMAGE_REGISTRY" ]; then
    echo "ERROR: IMAGE_REGISTRY environment variable not set"
    echo "Example: export IMAGE_REGISTRY=ghcr.io/your-username"
    exit 1
fi

if [ -z "$NEXT_PUBLIC_CHATBOT_API_URL" ]; then
    echo "ERROR: NEXT_PUBLIC_CHATBOT_API_URL environment variable not set"
    echo "Example: export NEXT_PUBLIC_CHATBOT_API_URL=https://api.yourdomain.com"
    exit 1
fi

echo "=== Building and Pushing Prism Container Images ==="
echo "Registry: $IMAGE_REGISTRY"
echo "Frontend API URL: $NEXT_PUBLIC_CHATBOT_API_URL"
echo

# Navigate to repo root
cd "$(dirname "$0")/../.."

echo "1. Building chatbot-api..."
docker build -t $IMAGE_REGISTRY/prism-chatbot-api:latest \
  -f services/chatbot-api/Dockerfile .

echo "2. Building ingestion-api..."
docker build -t $IMAGE_REGISTRY/prism-ingestion-api:latest \
  -f services/ingestion-api/Dockerfile .

echo "3. Building workers (shared image)..."
docker build -t $IMAGE_REGISTRY/prism-workers:latest \
  -f services/workers/Dockerfile .

echo "4. Building chatbot-ui..."
docker build -t $IMAGE_REGISTRY/prism-chatbot-ui:latest \
  --build-arg NEXT_PUBLIC_CHATBOT_API_URL=$NEXT_PUBLIC_CHATBOT_API_URL \
  ./web

echo
echo "=== Pushing images to registry ==="

echo "Pushing chatbot-api..."
docker push $IMAGE_REGISTRY/prism-chatbot-api:latest

echo "Pushing ingestion-api..."
docker push $IMAGE_REGISTRY/prism-ingestion-api:latest

echo "Pushing workers..."
docker push $IMAGE_REGISTRY/prism-workers:latest

echo "Pushing chatbot-ui..."
docker push $IMAGE_REGISTRY/prism-chatbot-ui:latest

echo
echo "=== Build and Push Complete ==="
echo "Images pushed:"
echo "  - $IMAGE_REGISTRY/prism-chatbot-api:latest"
echo "  - $IMAGE_REGISTRY/prism-ingestion-api:latest"
echo "  - $IMAGE_REGISTRY/prism-workers:latest"
echo "  - $IMAGE_REGISTRY/prism-chatbot-ui:latest"
echo
echo "Next steps:"
echo "  1. Update k8s manifests with your IMAGE_REGISTRY:"
echo "     find k8s/ -name '*.yaml' -exec sed -i \"s|IMAGE_REGISTRY|$IMAGE_REGISTRY|g\" {} \\;"
echo "  2. Deploy to k8s:"
echo "     ./k8s/scripts/apply-all.sh"

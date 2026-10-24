#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
GCLOUD=${GCLOUD:-/Users/evan/Downloads/google-cloud-sdk/bin/gcloud}
PROJECT=musial-family-public
REGION=us-east4
REPOSITORY=files-shortlinks
IMAGE_NAME=redirector
SERVICE=files-shortlinks
TAG=${1:-$(date -u +%Y%m%d-%H%M%S)}
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPOSITORY/$IMAGE_NAME"

"$GCLOUD" builds submit "$APP_DIR" \
  --tag="$IMAGE:$TAG" \
  --project="$PROJECT" \
  --quiet

DIGEST=$("$GCLOUD" artifacts docker images describe "$IMAGE:$TAG" \
  --project="$PROJECT" \
  --format='value(image_summary.digest)')

case "$DIGEST" in
  sha256:*) ;;
  *)
    echo "Could not resolve an immutable image digest for $IMAGE:$TAG" >&2
    exit 1
    ;;
esac

"$GCLOUD" run deploy "$SERVICE" \
  --image="$IMAGE@$DIGEST" \
  --region="$REGION" \
  --platform=managed \
  --service-account=files-shortlinks-runtime@$PROJECT.iam.gserviceaccount.com \
  --allow-unauthenticated \
  --ingress=internal-and-cloud-load-balancing \
  --min=1 \
  --max=10 \
  --concurrency=80 \
  --cpu=1 \
  --memory=512Mi \
  --timeout=10 \
  --execution-environment=gen1 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT,FIRESTORE_DATABASE=files-shortlinks,SHORTLINK_COLLECTION=short_links,CANONICAL_HOST=files.musial.io" \
  --description='Durable redirect resolver for files.musial.io/s/' \
  --project="$PROJECT" \
  --quiet

echo "Deployed $SERVICE from $IMAGE@$DIGEST"

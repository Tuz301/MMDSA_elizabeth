#!/usr/bin/env bash
# Ship the supervisor dashboard: build, sync, invalidate.
#
#   ./scripts/deploy-dashboard.sh <env>
#
# VITE_API_BASE and the Cognito ids come from dashboard/.env.production,
# which is deployment configuration and never committed.

set -euo pipefail

ENV_NAME="${1:?usage: deploy-dashboard.sh <env>}"
REGION="${AWS_REGION:-af-south-1}"

BUCKET=$(aws cloudformation describe-stacks --region "$REGION" \
  --stack-name "Mmdsa-${ENV_NAME}-App" \
  --query "Stacks[0].Outputs[?OutputKey=='DashboardBucketName'].OutputValue" \
  --output text)
URL=$(aws cloudformation describe-stacks --region "$REGION" \
  --stack-name "Mmdsa-${ENV_NAME}-App" \
  --query "Stacks[0].Outputs[?OutputKey=='DashboardUrl'].OutputValue" \
  --output text)
[ -n "$BUCKET" ] || { echo "No DashboardBucketName output. Is the App stack deployed?" >&2; exit 1; }

(cd dashboard && npm ci && npm run build)

# Hashed assets cache forever; index.html must always revalidate, because it
# is the pointer to everything else.
aws s3 sync dashboard/dist "s3://${BUCKET}" --delete \
  --cache-control "public,max-age=31536000,immutable" \
  --exclude index.html
aws s3 cp dashboard/dist/index.html "s3://${BUCKET}/index.html" \
  --cache-control "no-cache"

DISTRIBUTION_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?DomainName=='${URL#https://}'].Id | [0]" \
  --output text)
if [ -n "$DISTRIBUTION_ID" ] && [ "$DISTRIBUTION_ID" != "None" ]; then
  aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" \
    --paths "/index.html" > /dev/null
fi

echo "Dashboard deployed: ${URL}"

#!/usr/bin/env bash
# Ship a backend release: build, push, move the pointer, refresh the fleet.
#
#   ./scripts/deploy-app.sh <env> <tag>
#   ./scripts/deploy-app.sh pilot v1.0.0
#
# Rollback is this same script with the previous tag.

set -euo pipefail

ENV_NAME="${1:?usage: deploy-app.sh <env> <tag>}"
TAG="${2:?usage: deploy-app.sh <env> <tag>}"
REGION="${AWS_REGION:-af-south-1}"

REPO_URI=$(aws cloudformation describe-stacks --region "$REGION" \
  --stack-name "Mmdsa-${ENV_NAME}-App" \
  --query "Stacks[0].Outputs[?OutputKey=='AppRepositoryUri'].OutputValue" \
  --output text)
[ -n "$REPO_URI" ] || { echo "No AppRepositoryUri output. Is the App stack deployed?" >&2; exit 1; }

echo "Building ${REPO_URI}:${TAG} (linux/arm64, Graviton fleet)..."
docker build --platform linux/arm64 -t "${REPO_URI}:${TAG}" backend/

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${REPO_URI%%/*}"
docker push "${REPO_URI}:${TAG}"

echo "Moving the release pointer..."
aws ssm put-parameter --region "$REGION" \
  --name "/mmdsa/${ENV_NAME}/app-image-tag" \
  --value "$TAG" --type String --overwrite

ASG_NAME=$(aws autoscaling describe-auto-scaling-groups --region "$REGION" \
  --query "AutoScalingGroups[?contains(AutoScalingGroupName, 'Mmdsa-${ENV_NAME}-App')].AutoScalingGroupName | [0]" \
  --output text)
echo "Starting instance refresh on ${ASG_NAME}..."
aws autoscaling start-instance-refresh --region "$REGION" \
  --auto-scaling-group-name "$ASG_NAME" \
  --preferences MinHealthyPercentage=50,InstanceWarmup=300

echo "Release ${TAG} is rolling out. Watch: aws autoscaling describe-instance-refreshes --auto-scaling-group-name ${ASG_NAME} --region ${REGION}"

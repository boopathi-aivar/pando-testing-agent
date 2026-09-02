#!/bin/bash
# Pando Invoice Testing Agent — Deployment Script
# Prerequisites: aws cli, sam cli, docker (running)

set -e

AWS_REGION="us-east-1"
STACK_NAME="invoice-testing-agent"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo ""
echo "======================================================"
echo " Invoice Testing Agent — Deploy"
echo " Region  : ${AWS_REGION}"
echo " Account : ${AWS_ACCOUNT_ID}"
echo " Stack   : ${STACK_NAME}"
echo "======================================================"
echo ""

# ── Step 1: Collect config values ─────────────────────────────────────────────
read -p "INTAKE_API_KEY (shared secret with invoice_processor): " INTAKE_KEY
read -p "JWT_SECRET_KEY (press Enter for default): " JWT_SECRET
JWT_SECRET="${JWT_SECRET:-invoice-testing-agent-secret}"

# ── Step 2: Build Docker image ────────────────────────────────────────────────
echo ""
echo "Building Docker image..."
sam build

# ── Step 3: Deploy ────────────────────────────────────────────────────────────
echo ""
echo "Deploying to AWS..."
sam deploy \
  --stack-name "${STACK_NAME}" \
  --region "${AWS_REGION}" \
  --capabilities CAPABILITY_IAM \
  --resolve-image-repos \
  --no-confirm-changeset \
  --parameter-overrides \
    IntakeApiKey="${INTAKE_KEY}" \
    JwtSecretKey="${JWT_SECRET}" \
    AwsRegion="${AWS_REGION}"

# ── Step 4: Print outputs ─────────────────────────────────────────────────────
echo ""
echo "======================================================"
echo " Deployment complete!"
echo "======================================================"

API_URL=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${AWS_REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text)

echo ""
echo " API Gateway URL : ${API_URL}"
echo ""
echo " Next steps:"
echo "  1. Set VITE_API_URL=${API_URL} in Amplify Console → Environment Variables"
echo "  2. Set TESTING_AGENT_URL=${API_URL} in invoice_processor Lambda env vars"
echo "  3. Set TESTING_AGENT_API_KEY=${INTAKE_KEY} in invoice_processor Lambda env vars"
echo ""

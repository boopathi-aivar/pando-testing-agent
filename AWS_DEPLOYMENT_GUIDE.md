# AWS Serverless Deployment Guide — Invoice Testing Agent

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        USERS                                 │
└───────────────────────┬─────────────────────────────────────┘
                        │ HTTPS
          ┌─────────────▼──────────────┐
          │    CloudFront (CDN)         │ ← Frontend SPA (HTTPS + Cache)
          └─────────────┬──────────────┘
                        │
          ┌─────────────▼──────────────┐
          │    S3 Bucket (Static)       │ ← React build output
          └────────────────────────────┘

          ┌─────────────────────────────┐
          │  API Gateway HTTP API        │ ← HTTPS endpoint for backend
          └─────────────┬───────────────┘
                        │ invoke
          ┌─────────────▼───────────────┐
          │  Lambda: API Function        │ ← FastAPI via Mangum adapter
          │  (Container Image, 30s TO)   │   ECR image with PyMuPDF
          └──────┬──────────────────────┘
                 │ send message
          ┌──────▼──────────────────────┐
          │  SQS Queue (Job Queue)       │ ← Decouples long-running jobs
          └──────┬──────────────────────┘
                 │ trigger
          ┌──────▼──────────────────────┐
          │  Lambda: Worker Function     │ ← Runs Strands agent (15m TO)
          │  (Container Image, 15m TO)   │   Orchestrator + Validator
          └──────┬──────────────────────┘
                 │ read/write
          ┌──────▼──────────────────────┐
          │  MongoDB Atlas (Cloud DB)    │ ← No change needed
          └─────────────────────────────┘

          ┌─────────────────────────────┐
          │  AWS Secrets Manager         │ ← All secrets (replaces .env)
          └─────────────────────────────┘

          ┌─────────────────────────────┐
          │  ECR (Container Registry)    │ ← Docker images for both Lambdas
          └─────────────────────────────┘

          ┌─────────────────────────────┐
          │  S3 Buckets (File Storage)   │ ← Existing mapping files (no change)
          └─────────────────────────────┘

          ┌─────────────────────────────┐
          │  CloudWatch Logs             │ ← Existing Lambda logs (no change)
          └─────────────────────────────┘
```

**Why Lambda + SQS instead of a single Lambda?**
The `/api/projects/{project_id}/run-test` endpoint currently starts a background thread.
Lambda does not support background threads after the response is sent, and API Gateway
has a hard 29-second timeout. SQS decouples the API response (instant) from the
long-running Strands agent job (up to 15 minutes).

---

## Services to Provision

| Service | Purpose | Cost Model |
|---|---|---|
| S3 (frontend bucket) | Host static React build | ~$0.023/GB |
| CloudFront | HTTPS, CDN, cache for frontend | ~$0.01/10k requests |
| ECR | Store Docker images for Lambda | $0.10/GB/month |
| Lambda (API) | FastAPI via Mangum | Pay per invocation |
| Lambda (Worker) | Strands agent job runner | Pay per invocation |
| API Gateway HTTP API | Route HTTP requests to Lambda | $1/million requests |
| SQS | Job queue between API and Worker | $0.40/million messages |
| Secrets Manager | Store .env secrets securely | $0.40/secret/month |
| IAM Roles | Lambda permissions | Free |

---

## Prerequisites

- AWS CLI installed and configured (`aws configure`)
- Docker installed (for building Lambda container images)
- Node.js >= 18 and Python >= 3.11
- An AWS account with admin permissions
- MongoDB Atlas cluster already running (no changes needed to Atlas itself)

```bash
# Verify prerequisites
aws --version
docker --version
node --version
python3 --version
```

Set your AWS account ID and region as shell variables — you will use them throughout:

```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=ap-south-1          # change to your preferred region
export APP_NAME=invoice-testing-agent
echo "Account: $AWS_ACCOUNT_ID  Region: $AWS_REGION"
```

---

## Part 1: Code Changes Required

These are all the changes you must make before building and deploying.

### 1.1 Add Mangum to requirements.txt

Mangum is the ASGI adapter that wraps FastAPI for Lambda.

**File: `backend/requirements.txt`** — add this line:
```
mangum>=0.17.0
```

### 1.2 Create Lambda handler file

**Create new file: `backend/lambda_handler.py`**
```python
from mangum import Mangum
from main import app

# API Lambda entry point — wraps FastAPI for API Gateway
handler = Mangum(app, lifespan="off")
```

### 1.3 Create SQS Worker file

**Create new file: `backend/sqs_worker.py`**
```python
import json
import asyncio
from database import ensure_indexes
from agent_runner import run_agent_job

def handler(event, context):
    """SQS Worker Lambda — processes one job message per invocation."""
    ensure_indexes()
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        job_id = body["job_id"]
        project_id = body["project_id"]
        source = body.get("source", "ui_run")
        asyncio.run(run_agent_job(job_id, project_id, source))
```

### 1.4 Update `backend/routers/jobs.py` — replace background thread with SQS

Find the section in `jobs.py` where `threading.Thread` is used and replace it:

**Remove this pattern:**
```python
import threading

thread = threading.Thread(
    target=run_test_worker,
    args=[job_id, project_id, ...],
    daemon=True
)
thread.start()
```

**Replace with:**
```python
import boto3
import json
import os

sqs = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "ap-south-1"))
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")

if SQS_QUEUE_URL:
    # Production: send to SQS
    sqs.send_message(
        QueueUrl=SQS_QUEUE_URL,
        MessageBody=json.dumps({
            "job_id": job_id,
            "project_id": project_id,
            "source": "ui_run",
        })
    )
else:
    # Local dev: use background thread as before
    import threading
    thread = threading.Thread(
        target=run_test_worker,
        args=[job_id, project_id, ...],
        daemon=True
    )
    thread.start()
```

This keeps local development working unchanged (no `SQS_QUEUE_URL` set locally).

### 1.5 Update CORS in `backend/main.py`

**Find this block:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Replace with:**
```python
import os

_frontend_url = os.getenv("FRONTEND_URL", "")
_allowed_origins = [o for o in [
    _frontend_url,
    "http://localhost:5173",
    "http://127.0.0.1:5173",
] if o]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 1.6 Update `backend/config.py` — add Secrets Manager support

Add this function to your config.py (call it at startup to pull secrets):

```python
import boto3
import json
import os

def load_secrets_from_aws():
    """Pull secrets from AWS Secrets Manager into environment variables.
    Only runs when SECRET_NAME env var is set (i.e., in Lambda)."""
    secret_name = os.getenv("SECRET_NAME")
    if not secret_name:
        return  # local dev — use .env file as usual

    client = boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION", "ap-south-1"))
    try:
        response = client.get_secret_value(SecretId=secret_name)
        secrets = json.loads(response["SecretString"])
        for key, value in secrets.items():
            os.environ.setdefault(key, value)
    except Exception as e:
        print(f"[WARNING] Could not load secrets from Secrets Manager: {e}")
```

Then call it at the top of `main.py`, before anything else:

```python
# Add to top of main.py, before other imports that read env vars
from config import load_secrets_from_aws
load_secrets_from_aws()
```

### 1.7 Create `backend/Dockerfile`

**Create new file: `backend/Dockerfile`**
```dockerfile
FROM public.ecr.aws/lambda/python:3.11

# PyMuPDF needs these native libs
RUN yum install -y libGL mesa-libGL glib2 && yum clean all

WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default: API handler (override for worker)
CMD ["lambda_handler.handler"]
```

### 1.8 Update frontend API base URL

**File: `frontend/src/api/client.js`**

Find where the base URL is set (likely `http://localhost:3001` or just `/api`) and make it configurable:

```javascript
// Replace any hardcoded localhost URL with:
const API_BASE = import.meta.env.VITE_API_URL || '';

// All fetch calls should use: `${API_BASE}/api/...`
```

### 1.9 Create `frontend/.env.production`

**Create new file: `frontend/.env.production`**
```
VITE_API_URL=https://YOUR_API_GATEWAY_ID.execute-api.ap-south-1.amazonaws.com
```

Replace `YOUR_API_GATEWAY_ID` with the actual ID after you create the API Gateway (Step 3.4).

---

## Part 2: AWS Secrets Manager Setup

Store all secrets here — no credentials in code or environment variables directly.

```bash
aws secretsmanager create-secret \
  --name "${APP_NAME}/production" \
  --region $AWS_REGION \
  --secret-string '{
    "MONGODB_URL": "mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?appName=<AppName>",
    "MONGODB_DB": "pando_testing_agent",
    "JWT_SECRET_KEY": "REPLACE_WITH_STRONG_RANDOM_SECRET_64_CHARS",
    "ANTHROPIC_API_KEY": "sk-ant-YOUR_KEY",
    "LOG_GROUP_PREFIX": "/aws/lambda/invoice-processor",
    "INTAKE_API_KEY": "REPLACE_WITH_STRONG_RANDOM_SECRET"
  }'
```

> **Security Note:** Rotate `JWT_SECRET_KEY` and `INTAKE_API_KEY` — use a strong random
> value (e.g., `openssl rand -hex 32`). Do NOT reuse the local dev values.

Get the secret ARN for later:
```bash
export SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id "${APP_NAME}/production" \
  --region $AWS_REGION \
  --query ARN --output text)
echo "Secret ARN: $SECRET_ARN"
```

---

## Part 3: IAM Role for Lambda

```bash
# Create trust policy
cat > /tmp/lambda-trust.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "lambda.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
EOF

# Create role
aws iam create-role \
  --role-name "${APP_NAME}-lambda-role" \
  --assume-role-policy-document file:///tmp/lambda-trust.json

# Attach basic Lambda execution (logs)
aws iam attach-role-policy \
  --role-name "${APP_NAME}-lambda-role" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Inline policy: S3, CloudWatch Logs (read), SQS, Secrets Manager
cat > /tmp/lambda-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:HeadObject", "s3:ListBucket"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["logs:DescribeLogGroups", "logs:DescribeLogStreams",
                 "logs:GetLogEvents", "logs:FilterLogEvents",
                 "logs:StartQuery", "logs:GetQueryResults"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["sqs:SendMessage", "sqs:ReceiveMessage",
                 "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "${SECRET_ARN}"
    },
    {
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel"],
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name "${APP_NAME}-lambda-role" \
  --policy-name "${APP_NAME}-lambda-policy" \
  --policy-document file:///tmp/lambda-policy.json

# Get role ARN
export LAMBDA_ROLE_ARN=$(aws iam get-role \
  --role-name "${APP_NAME}-lambda-role" \
  --query Role.Arn --output text)
echo "Role ARN: $LAMBDA_ROLE_ARN"
```

---

## Part 4: ECR Repository & Docker Build

```bash
# Create ECR repository
aws ecr create-repository \
  --repository-name "${APP_NAME}" \
  --region $AWS_REGION

export ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${APP_NAME}"
echo "ECR URI: $ECR_URI"

# Authenticate Docker to ECR
aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Build and push the image (run from backend/ directory)
cd /path/to/invoice-testing-agent/backend

docker build --platform linux/amd64 -t "${APP_NAME}:latest" .
docker tag "${APP_NAME}:latest" "${ECR_URI}:latest"
docker push "${ECR_URI}:latest"

echo "Image pushed: ${ECR_URI}:latest"
```

---

## Part 5: SQS Queue

```bash
aws sqs create-queue \
  --queue-name "${APP_NAME}-jobs" \
  --region $AWS_REGION \
  --attributes '{
    "VisibilityTimeout": "900",
    "MessageRetentionPeriod": "86400",
    "ReceiveMessageWaitTimeSeconds": "20"
  }'

export SQS_QUEUE_URL=$(aws sqs get-queue-url \
  --queue-name "${APP_NAME}-jobs" \
  --region $AWS_REGION \
  --query QueueUrl --output text)
echo "SQS URL: $SQS_QUEUE_URL"

export SQS_QUEUE_ARN=$(aws sqs get-queue-attributes \
  --queue-url $SQS_QUEUE_URL \
  --attribute-names QueueArn \
  --query Attributes.QueueArn --output text)
```

---

## Part 6: Lambda Functions

### 6.1 API Lambda (FastAPI via Mangum)

```bash
aws lambda create-function \
  --function-name "${APP_NAME}-api" \
  --package-type Image \
  --code ImageUri="${ECR_URI}:latest" \
  --role $LAMBDA_ROLE_ARN \
  --region $AWS_REGION \
  --timeout 30 \
  --memory-size 1024 \
  --environment "Variables={
    AWS_REGION=${AWS_REGION},
    SECRET_NAME=${APP_NAME}/production,
    SQS_QUEUE_URL=${SQS_QUEUE_URL},
    FRONTEND_URL=https://PLACEHOLDER_REPLACE_AFTER_CLOUDFRONT,
    ENVIRONMENT=production
  }"
```

### 6.2 Worker Lambda (Strands Agent Job Runner)

The worker uses the same Docker image but a different CMD:

```bash
aws lambda create-function \
  --function-name "${APP_NAME}-worker" \
  --package-type Image \
  --code ImageUri="${ECR_URI}:latest" \
  --role $LAMBDA_ROLE_ARN \
  --region $AWS_REGION \
  --timeout 900 \
  --memory-size 2048 \
  --image-config '{"Command": ["sqs_worker.handler"]}' \
  --environment "Variables={
    AWS_REGION=${AWS_REGION},
    SECRET_NAME=${APP_NAME}/production,
    ENVIRONMENT=production
  }"

# Wire SQS queue to trigger the worker Lambda
aws lambda create-event-source-mapping \
  --function-name "${APP_NAME}-worker" \
  --event-source-arn $SQS_QUEUE_ARN \
  --batch-size 1 \
  --region $AWS_REGION
```

---

## Part 7: API Gateway HTTP API

```bash
# Create HTTP API
export API_ID=$(aws apigatewayv2 create-api \
  --name "${APP_NAME}-api" \
  --protocol-type HTTP \
  --region $AWS_REGION \
  --query ApiId --output text)
echo "API ID: $API_ID"

# Create Lambda integration
export INTEGRATION_ID=$(aws apigatewayv2 create-integration \
  --api-id $API_ID \
  --integration-type AWS_PROXY \
  --integration-uri "arn:aws:lambda:${AWS_REGION}:${AWS_ACCOUNT_ID}:function:${APP_NAME}-api" \
  --payload-format-version "2.0" \
  --region $AWS_REGION \
  --query IntegrationId --output text)

# Create catch-all route
aws apigatewayv2 create-route \
  --api-id $API_ID \
  --route-key "ANY /{proxy+}" \
  --target "integrations/${INTEGRATION_ID}" \
  --region $AWS_REGION

# Create root route
aws apigatewayv2 create-route \
  --api-id $API_ID \
  --route-key "ANY /" \
  --target "integrations/${INTEGRATION_ID}" \
  --region $AWS_REGION

# Deploy to $default stage
aws apigatewayv2 create-stage \
  --api-id $API_ID \
  --stage-name '$default' \
  --auto-deploy \
  --region $AWS_REGION

# Allow API Gateway to invoke the Lambda
aws lambda add-permission \
  --function-name "${APP_NAME}-api" \
  --statement-id apigateway-invoke \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:${AWS_REGION}:${AWS_ACCOUNT_ID}:${API_ID}/*" \
  --region $AWS_REGION

export API_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com"
echo "API Gateway URL: $API_URL"
```

---

## Part 8: Frontend — S3 + CloudFront

### 8.1 Build the frontend

```bash
cd /path/to/invoice-testing-agent/frontend

# Set production API URL (use the API Gateway URL from Part 7)
echo "VITE_API_URL=${API_URL}" > .env.production

npm install
npm run build
# Output is in dist/
```

### 8.2 Create S3 bucket for hosting

```bash
export FRONTEND_BUCKET="${APP_NAME}-frontend-$(echo $RANDOM | md5sum | head -c 8)"
echo "Bucket name: $FRONTEND_BUCKET"

aws s3 mb "s3://${FRONTEND_BUCKET}" --region $AWS_REGION

# Block all public access (CloudFront will handle access via OAC)
aws s3api put-public-access-block \
  --bucket $FRONTEND_BUCKET \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

### 8.3 Create CloudFront distribution

```bash
# Create Origin Access Control
export OAC_ID=$(aws cloudfront create-origin-access-control \
  --origin-access-control-config '{
    "Name": "'"${APP_NAME}-oac"'",
    "Description": "OAC for invoice testing agent frontend",
    "SigningProtocol": "sigv4",
    "SigningBehavior": "always",
    "OriginAccessControlOriginType": "s3"
  }' \
  --query OriginAccessControl.Id --output text)

# Create CloudFront distribution
export CF_DIST_ID=$(aws cloudfront create-distribution \
  --distribution-config '{
    "CallerReference": "'"${APP_NAME}-$(date +%s)"'",
    "Comment": "Invoice Testing Agent Frontend",
    "DefaultCacheBehavior": {
      "TargetOriginId": "s3-origin",
      "ViewerProtocolPolicy": "redirect-to-https",
      "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
      "AllowedMethods": {
        "Quantity": 2,
        "Items": ["GET", "HEAD"]
      }
    },
    "Origins": {
      "Quantity": 1,
      "Items": [{
        "Id": "s3-origin",
        "DomainName": "'"${FRONTEND_BUCKET}.s3.${AWS_REGION}.amazonaws.com"'",
        "S3OriginConfig": { "OriginAccessIdentity": "" },
        "OriginAccessControlId": "'"${OAC_ID}"'"
      }]
    },
    "DefaultRootObject": "index.html",
    "CustomErrorResponses": {
      "Quantity": 1,
      "Items": [{
        "ErrorCode": 403,
        "ResponsePagePath": "/index.html",
        "ResponseCode": "200",
        "ErrorCachingMinTTL": 0
      }]
    },
    "Enabled": true,
    "HttpVersion": "http2"
  }' \
  --query Distribution.Id --output text)

export CF_DOMAIN=$(aws cloudfront get-distribution \
  --id $CF_DIST_ID \
  --query Distribution.DomainName --output text)
echo "CloudFront domain: https://${CF_DOMAIN}"
```

### 8.4 Grant CloudFront access to S3 bucket

```bash
aws s3api put-bucket-policy \
  --bucket $FRONTEND_BUCKET \
  --policy '{
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "AllowCloudFrontOAC",
      "Effect": "Allow",
      "Principal": { "Service": "cloudfront.amazonaws.com" },
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::'"${FRONTEND_BUCKET}"'/*",
      "Condition": {
        "StringEquals": {
          "AWS:SourceArn": "arn:aws:cloudfront::'"${AWS_ACCOUNT_ID}"':distribution/'"${CF_DIST_ID}"'"
        }
      }
    }]
  }'
```

### 8.5 Upload frontend build to S3

```bash
cd /path/to/invoice-testing-agent/frontend

aws s3 sync dist/ "s3://${FRONTEND_BUCKET}/" \
  --delete \
  --cache-control "public, max-age=31536000, immutable" \
  --exclude "index.html"

# index.html: no cache (so new deploys take effect immediately)
aws s3 cp dist/index.html "s3://${FRONTEND_BUCKET}/index.html" \
  --cache-control "no-cache, no-store, must-revalidate"

echo "Frontend live at: https://${CF_DOMAIN}"
```

---

## Part 9: Update Lambda — Set FRONTEND_URL

After CloudFront is created, update the API Lambda's FRONTEND_URL env var:

```bash
aws lambda update-function-configuration \
  --function-name "${APP_NAME}-api" \
  --region $AWS_REGION \
  --environment "Variables={
    AWS_REGION=${AWS_REGION},
    SECRET_NAME=${APP_NAME}/production,
    SQS_QUEUE_URL=${SQS_QUEUE_URL},
    FRONTEND_URL=https://${CF_DOMAIN},
    ENVIRONMENT=production
  }"
```

---

## Part 10: MongoDB Atlas Configuration

1. Log into MongoDB Atlas → **Network Access** → **Add IP Address**
2. Add `0.0.0.0/0` (allow all) — Lambda IPs are dynamic and cannot be whitelisted by IP
3. Alternatively (more secure): Set up **AWS PrivateLink** between your VPC and Atlas

> For production, `0.0.0.0/0` is acceptable when you use strong credentials and TLS
> (MongoDB Atlas enforces TLS by default).

---

## Part 11: Environment Variables — Full Reference

### What changes from local to AWS

| Variable | Local (.env) | AWS (Secrets Manager) | Notes |
|---|---|---|---|
| `MONGODB_URL` | `mongodb+srv://...` | Same value, stored in Secret | No change |
| `MONGODB_DB` | `pando_testing_agent` | Same value | No change |
| `JWT_SECRET_KEY` | Weak local value | **Rotate to new strong value** | `openssl rand -hex 32` |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Same value, stored in Secret | No change |
| `INTAKE_API_KEY` | `pando-intake-secret-2024` | **Rotate to new value** | `openssl rand -hex 32` |
| `LOG_GROUP_PREFIX` | `/aws/lambda/invoice-processor` | Same value | No change |
| `AWS_ACCESS_KEY_ID` | Hardcoded in .env | **REMOVE** — use IAM Role | Lambda has implicit IAM role |
| `AWS_SECRET_ACCESS_KEY` | Hardcoded in .env | **REMOVE** — use IAM Role | Lambda has implicit IAM role |
| `AWS_REGION` | `us-east-1` | Set as Lambda env var | Not in Secret |

### New variables added for AWS

| Variable | Where Set | Value |
|---|---|---|
| `SECRET_NAME` | Lambda env var | `invoice-testing-agent/production` |
| `SQS_QUEUE_URL` | Lambda env var (API only) | SQS queue URL from Part 5 |
| `FRONTEND_URL` | Lambda env var (API only) | CloudFront domain URL |
| `ENVIRONMENT` | Lambda env var | `production` |

### Frontend variable

| Variable | File | Value |
|---|---|---|
| `VITE_API_URL` | `frontend/.env.production` | API Gateway URL from Part 7 |

---

## Part 12: Redeployment (After Code Changes)

Each time you update backend code:

```bash
cd /path/to/invoice-testing-agent/backend

# Rebuild and push image
docker build --platform linux/amd64 -t "${APP_NAME}:latest" .
docker tag "${APP_NAME}:latest" "${ECR_URI}:latest"
docker push "${ECR_URI}:latest"

# Update both Lambda functions to use new image
aws lambda update-function-code \
  --function-name "${APP_NAME}-api" \
  --image-uri "${ECR_URI}:latest" \
  --region $AWS_REGION

aws lambda update-function-code \
  --function-name "${APP_NAME}-worker" \
  --image-uri "${ECR_URI}:latest" \
  --region $AWS_REGION
```

Each time you update frontend code:

```bash
cd /path/to/invoice-testing-agent/frontend
npm run build

aws s3 sync dist/ "s3://${FRONTEND_BUCKET}/" --delete \
  --cache-control "public, max-age=31536000, immutable" --exclude "index.html"
aws s3 cp dist/index.html "s3://${FRONTEND_BUCKET}/index.html" \
  --cache-control "no-cache, no-store, must-revalidate"

# Invalidate CloudFront cache
aws cloudfront create-invalidation \
  --distribution-id $CF_DIST_ID \
  --paths "/*"
```

---

## Part 13: Verify Deployment

```bash
# 1. Health check
curl "https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/health"
# Expected: {"status":"healthy"}

# 2. API root
curl "https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/"
# Expected: {"status":"ok","service":"pando-testing-agent","version":"2.0.0"}

# 3. Frontend
open "https://${CF_DOMAIN}"
# Should show the login page

# 4. Login
curl -X POST "https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"pando@aivar.tech","password":"pando@123"}'
# Expected: {"access_token":"...","token_type":"bearer"}
```

---

## Part 14: Security Checklist

- [ ] `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` removed from code and .env — Lambda uses IAM role
- [ ] `JWT_SECRET_KEY` rotated to a new strong secret
- [ ] `INTAKE_API_KEY` rotated to a new strong secret
- [ ] MongoDB Atlas password changed for production (create a separate Atlas user)
- [ ] `.env` file never committed to git (add to `.gitignore`)
- [ ] Lambda functions have minimal IAM permissions (no `*` on sensitive services)
- [ ] API Gateway has throttling configured (optional but recommended)
- [ ] CloudFront forces HTTPS (configured via `redirect-to-https`)

---

## Part 15: Cost Estimate

For low-to-medium usage (thousands of tests/month):

| Service | Estimate |
|---|---|
| Lambda (API + Worker) | < $1/month |
| API Gateway | < $1/month |
| S3 (frontend) | < $0.10/month |
| CloudFront | < $1/month |
| SQS | < $0.01/month |
| Secrets Manager | ~$0.40/month (1 secret) |
| ECR | ~$0.10/month |
| **Total** | **~$3–5/month** |

MongoDB Atlas M0 (free tier) is $0/month for dev; upgrade to M10+ for production.

---

## Troubleshooting

**Lambda cold start errors (MongoDB connection)**
MongoDB connections can time out on cold start. In `database.py`, ensure you are reusing
the Motor client across invocations by initializing it at module level (not inside
request handlers).

**API Gateway 502 errors**
Check Lambda CloudWatch logs: `aws logs tail /aws/lambda/${APP_NAME}-api --follow`
Usually caused by import errors or missing environment variables.

**SQS worker not processing jobs**
Check: `aws lambda list-event-source-mappings --function-name ${APP_NAME}-worker`
Verify `State: Enabled`. Check worker logs: `aws logs tail /aws/lambda/${APP_NAME}-worker --follow`

**Frontend 403 from S3**
The bucket policy and CloudFront OAC must reference the same distribution ID.
Re-run Part 8.4 after CloudFront distribution is created.

**CORS errors in browser**
Verify `FRONTEND_URL` in API Lambda env matches the exact CloudFront domain (with `https://`).
Check `allow_origins` list in `main.py`.

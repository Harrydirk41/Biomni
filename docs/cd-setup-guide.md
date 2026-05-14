# CD Setup Guide — Biomni on AWS

This guide walks through the one-time steps to stand up the CD pipeline so that every push to `main` automatically builds and deploys the Biomni PKPD agent.

---

## Overview

```
bootstrap.sh        → VPC, subnets, security groups, EFS, ECR, IAM roles, Secrets
  ↓
docker build + push → First image in ECR (required before ECS service can start)
  ↓
setup-cd.sh         → OIDC trust, ECS cluster + service + ALB, Lambda function
  ↓
GitHub secrets set  → Pipeline can authenticate to AWS
  ↓
git push to main    → CI tests → Docker build → deploy (fully automated from here)
```

---

## Prerequisites

- AWS CLI configured (`aws sts get-caller-identity` returns your account)
- Docker installed and running
- `bash infra/bootstrap.sh` completed successfully (creates `infra/.env.generated`)

---

## Step 1 — Bootstrap (VPC + ECR + IAM)

If you haven't run bootstrap yet:

```bash
bash infra/bootstrap.sh
```

This creates:
- VPC with public + private subnets in two AZs
- Security groups (ALB, app, EFS)
- EFS file system + access point (for persistent `/data` across task restarts)
- ECR repository (`biomni`)
- IAM roles: `biomni-ecs-execution-role`, `biomni-task-role`, `biomni-lambda-role`
- Secrets Manager secret: `biomni/anthropic-key` (placeholder value)
- Writes `infra/.env.generated` with all IDs

---

## Step 2 — Push First Docker Image

ECS cannot create a service until at least one image exists in ECR. Push before running `setup-cd.sh`.

```bash
# Load ECR URI from bootstrap output
source infra/.env.generated

# Build and push
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin \
    "$(echo "$ECR_URI" | cut -d/ -f1)"

docker build -t "$ECR_URI:latest" -f deploy/Dockerfile .
docker push "$ECR_URI:latest"
```

---

## Step 3 — Create CD Infrastructure

```bash
source infra/.env.generated   # load IDs from bootstrap
bash infra/setup-cd.sh
```

This script is **idempotent** — safe to re-run if something fails partway through. It creates or skips:

| Resource | Name | Purpose |
|---|---|---|
| OIDC provider | `token.actions.githubusercontent.com` | Lets GitHub Actions assume an IAM role |
| IAM role | `github-oidc-biomni` | Role GitHub Actions assumes (no stored keys) |
| ECS cluster | `biomni-cluster` | Fargate + Fargate Spot capacity |
| ALB | `biomni-alb` | Internet-facing, port 80 → port 8000 |
| Target group | `biomni-tg` | IP-based, health check on `/health` |
| Task definition | `biomni` | 2 vCPU / 8 GB, EFS + Secrets Manager |
| ECS service | `biomni-api` | 1 replica, private subnets, rolls via ALB |
| Lambda function | `biomni-agent` | Container image, 900s timeout, 3 GB |

At the end of the script, the ALB DNS name and all required GitHub secrets are printed.

---

## Step 4 — Store Anthropic API Key

```bash
aws secretsmanager put-secret-value \
  --secret-id biomni/anthropic-key \
  --secret-string 'sk-ant-XXXXXXXXXXXXXXXX'
```

The ECS task definition already references this secret by ARN — the container receives it as `ANTHROPIC_API_KEY` at startup.

---

## Step 5 — Set GitHub Secrets

Go to **GitHub → Settings → Secrets and variables → Actions** and add:

| Secret | Value (from setup-cd.sh output) |
|---|---|
| `AWS_ROLE_ARN` | `arn:aws:iam::314567760197:role/github-oidc-biomni` |
| `AWS_REGION` | `us-west-2` |
| `ECR_REPOSITORY` | `biomni` |
| `DEPLOY_TARGET` | `fargate` |
| `ECS_CLUSTER` | `biomni-cluster` |
| `ECS_SERVICE_FARGATE` | `biomni-api` |
| `LAMBDA_FUNCTION_NAME` | `biomni-agent` |
| `LANGCHAIN_API_KEY` | `ls__...` (for weekly benchmark, optional) |

Optional (only if using EKS):

| Secret | Value |
|---|---|
| `EKS_CLUSTER_NAME` | `biomni` |

---

## Step 6 — Trigger First CD Run

```bash
git commit --allow-empty -m "chore: trigger first CD run"
git push origin main
```

Watch the pipeline at **GitHub → Actions**.

The pipeline runs:
1. **Lint** — ruff on all PKPD Python files
2. **Test** — 83 pytest tests (no LLM, no R, < 60 s)
3. **Build** — Docker image → ECR (tagged with git SHA + `latest`)
4. **Deploy** — rolling update to Fargate (waits for service stability)

After the deploy, the ALB serves traffic within ~2 minutes.

---

## Verifying the Deployment

```bash
source infra/.env.generated

# Check service is running
aws ecs describe-services \
  --cluster biomni-cluster \
  --services biomni-api \
  --query "services[0].{status:status,running:runningCount,desired:desiredCount}"

# Hit the health endpoint
curl http://$ALB_DNS/health
```

Expected response:
```json
{"status": "ok"}
```

---

## Ongoing CD Flow (Every Push to main)

```
git push origin main
         │
         ▼
    GitHub Actions
         │
    ┌────┴────┐
    │  lint   │  ruff check — fails fast
    └────┬────┘
         │
    ┌────┴────┐
    │  test   │  pytest 83 tests — gates the build
    └────┬────┘
         │
    ┌────┴────────────┐
    │  build + push   │  docker buildx → ECR:$SHA + ECR:latest
    └────┬────────────┘
         │
    ┌────┴─────────────┐
    │  deploy-fargate  │  render new task def → ECS rolling update
    └──────────────────┘
```

If any job fails, the **Notify** job prints a summary. No deployment happens if tests fail.

---

## Switching Deployment Targets

The `DEPLOY_TARGET` secret controls which deploy jobs run:

| Value | Jobs that run |
|---|---|
| `fargate` | ECS Fargate (recommended) |
| `lambda` | Lambda function update |
| `ec2` | ECS EC2/GPU tier |
| `eks` | Kubernetes rolling deploy |
| `all` | All four targets simultaneously |

To change the target without editing secrets, use **workflow_dispatch** in the Actions tab and set the `deploy_target` input.

---

## Rollback

```bash
# List recent task definition revisions
aws ecs list-task-definitions --family-prefix biomni --sort DESC

# Force service to use a previous revision
aws ecs update-service \
  --cluster biomni-cluster \
  --service biomni-api \
  --task-definition biomni:42    # replace 42 with desired revision
```

For Lambda:
```bash
# List versions
aws lambda list-versions-by-function --function-name biomni-agent

# Point live alias to a previous version
aws lambda update-alias \
  --function-name biomni-agent \
  --name live \
  --function-version 7    # replace with desired version
```

---

## Cost Estimate (Fargate, us-west-2)

| Resource | Spec | $/month (est.) |
|---|---|---|
| ECS Fargate | 2 vCPU / 8 GB, 1 task, 24×7 | ~$75 |
| ALB | 1 LCU baseline | ~$20 |
| ECR | ~5 GB images | ~$0.50 |
| EFS | minimal I/O | ~$1 |
| Secrets Manager | 1 secret | ~$0.40 |
| CloudWatch Logs | ~1 GB/month | ~$0.50 |
| **Total** | | **~$97/month** |

To reduce cost during off-hours: set `desired_count=0` and scale up before use.

```bash
# Scale down (stop all tasks)
aws ecs update-service \
  --cluster biomni-cluster \
  --service biomni-api \
  --desired-count 0

# Scale up
aws ecs update-service \
  --cluster biomni-cluster \
  --service biomni-api \
  --desired-count 1
```

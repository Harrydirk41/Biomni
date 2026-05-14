# CI/CD Guide — Biomni PKPD Agent

## What is CI/CD and When Do You Need It

CI/CD stands for **Continuous Integration / Continuous Deployment**.

- **CI (Continuous Integration):** Every time you push code, automated checks run — tests, linting, Docker build. You catch broken code before it reaches production.
- **CD (Continuous Deployment):** After CI passes, the new image is automatically deployed to your chosen AWS tier. No manual `docker build` or `aws ecs update-service` commands.

**You need CI/CD when:**
- More than one person is committing to the repo
- You're deploying to a real environment (staging or production)
- You want reproducible, auditable deployments (required for GxP/regulatory submissions)
- You're tired of running the same 10 manual commands every time you change a tool function

**You don't need it yet if:**
- You're still setting up AWS infrastructure for the first time
- You're the only developer and you're still experimenting locally

---

## How the Biomni Pipeline Works

```
Developer pushes to main
         │
         ▼
    ┌─────────┐
    │  test   │  ← lint + pytest (always runs on push + PR)
    └────┬────┘
         │ passes
         ▼
    ┌─────────┐
    │  build  │  ← docker build + push to ECR (push to main only)
    └────┬────┘
         │ image ready
    ┌────┴──────────────────────────────────┐
    │  parallel deploy jobs (based on       │
    │  DEPLOY_TARGET secret)                │
    │                                       │
    │  deploy-lambda   deploy-fargate       │
    │  deploy-ecs-ec2  deploy-eks           │
    └───────────────────────────────────────┘
         │ any job fails
         ▼
      notify  ← prints failure summary
```

The workflow file is at `.github/workflows/ci-cd.yml`.

---

## Triggers — When Does What Run

### On every `git push` to `main`
Runs the full pipeline: test → build → deploy.

```
git add biomni/tool/dmpk.py
git commit -m "fix CLint calculation for low protein concentrations"
git push origin main
```
→ CI runs tests, builds new Docker image tagged with the commit SHA, deploys to your configured tier.

### On every Pull Request to `main`
Runs **test only** — no build, no deploy. This is the safety gate before merging.

```
git checkout -b fix/nca-lambda-z
# make changes
git push origin fix/nca-lambda-z
# open PR on GitHub → test job runs automatically
```

### What is **skipped**
Pushes that only touch docs or markdown do not trigger a build or deploy:

```yaml
paths-ignore:
  - "docs/**"
  - "*.md"
```

So editing this guide, the README, or the deployment guide does not trigger a Docker build. Intentional — no need to rebuild a 10GB image for a typo fix.

---

## GitHub Secrets Required

Go to: **GitHub repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value | Required for |
|---|---|---|
| `AWS_ROLE_ARN` | `arn:aws:iam::314567760197:role/github-oidc-role` | All AWS steps |
| `AWS_REGION` | `us-west-2` | All AWS steps |
| `ECR_REPOSITORY` | `biomni` | Build job |
| `DEPLOY_TARGET` | `fargate` (or `lambda`, `ec2`, `eks`, `all`) | Controls which deploy job runs |
| `ECS_CLUSTER` | `biomni-cluster` | Fargate + EC2 deploy |
| `ECS_SERVICE_FARGATE` | `biomni-api` | Fargate deploy |
| `ECS_SERVICE_EC2` | `biomni-api-ec2` | EC2 deploy |
| `LAMBDA_FUNCTION_NAME` | `biomni-agent` | Lambda deploy |
| `EKS_CLUSTER_NAME` | `biomni` | EKS deploy |

### Setting up OIDC (no long-lived AWS keys)

OIDC lets GitHub Actions authenticate to AWS without storing an `AWS_ACCESS_KEY_ID` secret. The bootstrap script creates the IAM role — you just need to tell it to trust GitHub:

```bash
# Create the OIDC provider (one-time per AWS account)
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1

# Create the role that GitHub Actions will assume
aws iam create-role \
  --role-name github-oidc-role \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Federated": "arn:aws:iam::314567760197:oidc-provider/token.actions.githubusercontent.com"},
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:harrydirk41/biomni:*"
        }
      }
    }]
  }'

# Attach permissions the role needs
aws iam attach-role-policy \
  --role-name github-oidc-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser

aws iam attach-role-policy \
  --role-name github-oidc-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonECS_FullAccess

# Get the ARN to put in the secret
aws iam get-role --role-name github-oidc-role --query Role.Arn --output text
# → arn:aws:iam::314567760197:role/github-oidc-role
```

Add this ARN as the `AWS_ROLE_ARN` secret in GitHub.

---

## DEPLOY_TARGET — Choosing Your Tier

Set this secret to control which deployment job runs after a successful build.

| Value | What deploys | When to use |
|---|---|---|
| `lambda` | Lambda function image updated + `live` alias promoted | Short tasks, low traffic, cheapest |
| `fargate` | ECS Fargate service updated (rolling deploy) | Always-on API, no GPU needed |
| `ec2` | ECS EC2 service updated (GPU instances) | Need GPU for local model inference |
| `eks` | Kubernetes deployment image updated | Multi-team, need autoscaling + canary |
| `all` | All four run in parallel | Full multi-tier deployment |

**For the PKPD agent starting out:** set `DEPLOY_TARGET=fargate`. It's the simplest always-on option with no GPU needed (you're calling Bedrock, not running a local model).

---

## Deployment Job Details

### Job: test

Runs on every push and every PR. Installs minimal Python dependencies and runs pytest.

```yaml
- name: Install test dependencies
  run: pip install pytest pydantic langchain langgraph

- name: Run tests
  run: pytest tests/ -v --tb=short
```

**For the PKPD agent — add a tests/ directory:**

```
tests/
  test_dmpk.py          ← unit tests for CLint, PPB, Caco-2 functions
  test_poppk.py         ← NONMEM control stream generation
  test_cdisc_io.py      ← SDTM PC reader
  test_pkpd_agent.py    ← agent initialises without errors
```

Example test:

```python
# tests/test_dmpk.py
from biomni.tool.dmpk import calculate_microsomal_stability

def test_high_clearance_classification():
    result = calculate_microsomal_stability(
        time_points=[0, 5, 10, 20, 40, 60],
        percent_remaining=[100, 74.1, 54.9, 30.1, 9.1, 2.7],
        microsomal_protein_conc_mg_per_mL=0.5,
    )
    assert "HIGH" in result
    assert "CLint" in result

def test_low_clearance_classification():
    result = calculate_microsomal_stability(
        time_points=[0, 15, 30, 60, 90, 120],
        percent_remaining=[100, 89.5, 80.1, 64.2, 51.4, 41.2],
        microsomal_protein_conc_mg_per_mL=0.5,
    )
    assert "LOW" in result
```

### Job: build

Runs only on push to `main` (not on PRs — no point building an image for unmerged code).

Uses Docker Buildx with ECR layer caching:
- First build: ~30 min (conda env from scratch)
- Subsequent builds: ~3–5 min (conda layer cached in ECR as `:buildcache`)

The image is tagged with both:
- `:latest` — for human reference
- `:<git-sha>` — for exact reproducibility (required for GxP audit trails)

### Job: deploy-lambda

Updates the Lambda function to the new image, waits for the update, publishes a new version, and promotes it to the `live` alias. Production traffic always hits the `live` alias — this lets you roll back instantly by pointing the alias to an older version.

```bash
# Manual rollback if needed
aws lambda update-alias \
  --function-name biomni-agent \
  --name live \
  --function-version 42   # previous known-good version
```

### Job: deploy-fargate

Downloads the current ECS task definition, swaps the image URI, registers the new task definition, and triggers a rolling deployment. ECS replaces old containers with new ones while keeping traffic flowing.

```bash
# Check deploy status manually
aws ecs describe-services \
  --cluster biomni-cluster \
  --services biomni-api \
  --query "services[0].deployments"
```

### Job: deploy-eks

Updates the image in the Kubernetes deployment and waits for the rollout to complete. EKS does a rolling update — new pods come up before old ones are terminated.

```bash
# Check rollout status manually
kubectl rollout status deployment/biomni-api -n biomni

# Roll back if something is wrong
kubectl rollout undo deployment/biomni-api -n biomni
```

---

## PKPD Agent — Specific CI/CD Scenarios

### Scenario 1: You fixed a bug in CLint calculation

```bash
# Edit biomni/tool/dmpk.py
git add biomni/tool/dmpk.py
git commit -m "fix: CLint calculation uses correct volume scaling"
git push origin main
```

Pipeline:
1. `test` → runs `pytest tests/test_dmpk.py` — catches if your fix broke something else
2. `build` → new image tagged `biomni:<sha>`
3. `deploy-fargate` → rolling update, zero downtime

### Scenario 2: You added a new popPK model type

```bash
git add biomni/tool/poppk.py biomni/agent/pkpd_agent.py
git commit -m "feat: add 3cmt_oral with transit absorption"
git push origin main
```

Same pipeline. If the new model type breaks an existing test, CI catches it before deploy.

### Scenario 3: You updated a know-how skill document

```bash
git add biomni/know_how/pkpd/population_pk_diagnostics.md
git commit -m "docs: add NPDE interpretation for BLQ-heavy datasets"
git push origin main
```

Pipeline: **nothing runs** — `*.md` changes are in `paths-ignore`. This is correct; no rebuild needed for a document change.

### Scenario 4: You want to test a new prompt before merging

```bash
git checkout -b experiment/new-pkpd-context
# edit biomni/agent/pkpd_agent.py
git push origin experiment/new-pkpd-context
# open PR → only test job runs
# review LangSmith traces from dev agent
# if good, merge → full build + deploy runs
```

### Scenario 5: A deploy failed and you need to roll back

```bash
# Find the last known good image tag from ECR
aws ecr describe-images \
  --repository-name biomni \
  --query 'sort_by(imageDetails,&imagePushedAt)[-3:].imageTags' \
  --output table

# Force ECS to run a previous task definition revision
aws ecs update-service \
  --cluster biomni-cluster \
  --service biomni-api \
  --task-definition biomni:47   # previous revision number
```

### Scenario 6: Running the PKPD benchmark in CI

Add a scheduled benchmark job to the workflow to automatically track agent quality over time:

```yaml
# Add this to .github/workflows/ci-cd.yml

on:
  schedule:
    - cron: "0 2 * * 1"   # every Monday at 2 AM UTC

jobs:
  benchmark:
    name: PKPD Benchmark
    runs-on: ubuntu-latest
    if: github.event_name == 'schedule'
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: pip install langsmith langchain-anthropic langgraph
      - name: Run PKPD benchmark
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          LANGCHAIN_API_KEY: ${{ secrets.LANGCHAIN_API_KEY }}
          LANGCHAIN_TRACING_V2: "true"
          LANGCHAIN_PROJECT: "biomni-pkpd-ci"
        run: |
          python -m biomni.eval.pkpd_benchmark --run \
            --experiment "ci-$(date +%Y%m%d)" \
            --model claude-sonnet-4-20250514
```

This runs the 16-case benchmark every Monday and logs results to LangSmith under the `biomni-pkpd-ci` project. You can see week-over-week score trends in the LangSmith UI.

---

## Image Tagging Convention

| Tag | Example | Purpose |
|---|---|---|
| `:<git-sha>` | `biomni:a3f9c12` | Exact reproducibility — always know what code is running |
| `:latest` | `biomni:latest` | Convenience reference, not reliable for audits |
| `:buildcache` | `biomni:buildcache` | Docker layer cache stored in ECR, speeds up CI builds |

**For GxP / regulatory submissions:** always reference the `:<git-sha>` tag in your validation documents. It ties the deployed agent directly to a specific commit in your git history.

---

## Common CI/CD Problems and Fixes

| Problem | Cause | Fix |
|---|---|---|
| `Error: credentials expired` | OIDC token issue | Check `AWS_ROLE_ARN` secret is correct; verify OIDC provider exists |
| Build takes 40 min every time | Layer cache miss | First push after cache is cleared; subsequent builds use cache |
| `paths-ignore` not working | Pattern mismatch | Check pattern — `docs/**` matches subdirs, `*.md` matches root only |
| Deploy job skipped unexpectedly | `DEPLOY_TARGET` secret wrong | Go to Settings → Secrets, verify value is exactly `fargate` (no spaces) |
| `kubectl rollout` times out | New pod crashing on start | `kubectl logs -n biomni -l app=biomni-api --previous` to see crash logs |
| Lambda `live` alias not updating | Alias doesn't exist yet | First deploy creates it; re-run the failed job |
| ECR image limit hit | >20 images | Lifecycle policy keeps last 20 — bootstrap already set this up |

---

## Quick Reference

```bash
# Trigger a deploy manually (bypasses CI — use sparingly)
git commit --allow-empty -m "chore: trigger deploy"
git push origin main

# Watch the pipeline live
# GitHub → Actions tab → click the running workflow

# Check what image is currently running on Fargate
aws ecs describe-tasks \
  --cluster biomni-cluster \
  --tasks $(aws ecs list-tasks --cluster biomni-cluster --service-name biomni-api \
            --query "taskArns[0]" --output text) \
  --query "tasks[0].containers[0].image"

# Check what commit is deployed
IMAGE=$(aws ecs describe-tasks ...)   # from above
echo "${IMAGE##*:}"   # prints the git SHA
git log --oneline | grep "${IMAGE##*:}"
```

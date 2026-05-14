#!/usr/bin/env bash
# infra/setup-cd.sh
# Creates the remaining infrastructure needed for CD:
#   - GitHub OIDC trust (no long-lived AWS keys in GitHub)
#   - ECS Fargate cluster + ALB + task definition + service
#   - Lambda function (container image)
#
# Prerequisites:
#   1. bash infra/bootstrap.sh   (VPC, SGs, EFS, ECR, IAM roles, Secrets)
#   2. docker build + push to ECR at least once (for the initial image)
#
# Usage:
#   source infra/.env.generated   # load IDs from bootstrap
#   bash infra/setup-cd.sh
#
# After this script:
#   Set the GitHub secrets listed at the end, then push to main.
#   Every push to main will automatically build + deploy from that point on.

set -euo pipefail
AWS_PAGER=""
export AWS_PAGER

# ─────────────────────────────────────────────────────────────────────────────
# Load bootstrap outputs
# ─────────────────────────────────────────────────────────────────────────────

ENV_FILE="$(dirname "$0")/.env.generated"
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: infra/.env.generated not found. Run bootstrap.sh first."
  exit 1
fi
# shellcheck source=/dev/null
source "$ENV_FILE"

GITHUB_ORG="${GITHUB_ORG:-harrydirk41}"
GITHUB_REPO="${GITHUB_REPO:-biomni}"
PROJECT="biomni"
CLUSTER_NAME="$PROJECT-cluster"
SERVICE_NAME="$PROJECT-api"
TASK_FAMILY="$PROJECT"
LAMBDA_NAME="$PROJECT-agent"
ALB_NAME="$PROJECT-alb"
TG_NAME="$PROJECT-tg"
CONTAINER_PORT=8000

echo ">>> AWS Account : $AWS_ACCOUNT"
echo ">>> AWS Region  : $AWS_REGION"
echo ">>> ECR URI     : $ECR_URI"
echo ">>> GitHub      : $GITHUB_ORG/$GITHUB_REPO"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. GitHub OIDC provider + role
#    Allows GitHub Actions to call AWS without storing long-lived credentials.
# ─────────────────────────────────────────────────────────────────────────────
echo "=== [1/6] GitHub OIDC ==="

OIDC_PROVIDER_ARN="arn:aws:iam::$AWS_ACCOUNT:oidc-provider/token.actions.githubusercontent.com"

aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
  2>/dev/null || echo "  OIDC provider already exists"

OIDC_ROLE_NAME="github-oidc-biomni"

aws iam create-role \
  --role-name "$OIDC_ROLE_NAME" \
  --assume-role-policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Effect\": \"Allow\",
      \"Principal\": {
        \"Federated\": \"$OIDC_PROVIDER_ARN\"
      },
      \"Action\": \"sts:AssumeRoleWithWebIdentity\",
      \"Condition\": {
        \"StringLike\": {
          \"token.actions.githubusercontent.com:sub\": \"repo:$GITHUB_ORG/$GITHUB_REPO:*\"
        },
        \"StringEquals\": {
          \"token.actions.githubusercontent.com:aud\": \"sts.amazonaws.com\"
        }
      }
    }]
  }" 2>/dev/null || echo "  OIDC role already exists"

# Attach permissions the OIDC role needs to deploy
for POLICY in \
  arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser \
  arn:aws:iam::aws:policy/AmazonECS_FullAccess \
  arn:aws:iam::aws:policy/AWSLambda_FullAccess \
  arn:aws:iam::aws:policy/AmazonEKSClusterPolicy; do
  aws iam attach-role-policy \
    --role-name "$OIDC_ROLE_NAME" \
    --policy-arn "$POLICY" 2>/dev/null || true
done

OIDC_ROLE_ARN=$(aws iam get-role \
  --role-name "$OIDC_ROLE_NAME" \
  --query Role.Arn --output text)
echo "  OIDC role ARN: $OIDC_ROLE_ARN"

# ─────────────────────────────────────────────────────────────────────────────
# 2. ECS Fargate cluster
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [2/6] ECS Cluster ==="

aws ecs create-cluster \
  --cluster-name "$CLUSTER_NAME" \
  --capacity-providers FARGATE FARGATE_SPOT \
  --default-capacity-provider-strategy \
    capacityProvider=FARGATE,weight=1 \
  2>/dev/null || echo "  Cluster already exists"

echo "  Cluster: $CLUSTER_NAME"

# ─────────────────────────────────────────────────────────────────────────────
# 3. Application Load Balancer + target group + listener
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [3/6] ALB ==="

ALB_ARN=$(aws elbv2 describe-load-balancers \
  --names "$ALB_NAME" \
  --query "LoadBalancers[0].LoadBalancerArn" \
  --output text 2>/dev/null || echo "None")

if [ "$ALB_ARN" = "None" ] || [ -z "$ALB_ARN" ]; then
  ALB_ARN=$(aws elbv2 create-load-balancer \
    --name "$ALB_NAME" \
    --subnets "$PUB_SUB1" "$PUB_SUB2" \
    --security-groups "$ALB_SG" \
    --scheme internet-facing \
    --type application \
    --ip-address-type ipv4 \
    --query "LoadBalancers[0].LoadBalancerArn" \
    --output text)
  echo "  Created ALB: $ALB_ARN"
else
  echo "  Using existing ALB: $ALB_ARN"
fi

ALB_DNS=$(aws elbv2 describe-load-balancers \
  --load-balancer-arns "$ALB_ARN" \
  --query "LoadBalancers[0].DNSName" --output text)

# Target group (IP-based for Fargate)
TG_ARN=$(aws elbv2 describe-target-groups \
  --names "$TG_NAME" \
  --query "TargetGroups[0].TargetGroupArn" \
  --output text 2>/dev/null || echo "None")

if [ "$TG_ARN" = "None" ] || [ -z "$TG_ARN" ]; then
  TG_ARN=$(aws elbv2 create-target-group \
    --name "$TG_NAME" \
    --protocol HTTP \
    --port "$CONTAINER_PORT" \
    --vpc-id "$VPC_ID" \
    --target-type ip \
    --health-check-path /health \
    --health-check-interval-seconds 30 \
    --healthy-threshold-count 2 \
    --unhealthy-threshold-count 3 \
    --query "TargetGroups[0].TargetGroupArn" \
    --output text)
  echo "  Created target group: $TG_ARN"
else
  echo "  Using existing target group: $TG_ARN"
fi

# HTTP listener (port 80 → target group)
aws elbv2 create-listener \
  --load-balancer-arn "$ALB_ARN" \
  --protocol HTTP \
  --port 80 \
  --default-actions "Type=forward,TargetGroupArn=$TG_ARN" \
  2>/dev/null || echo "  Listener already exists"

echo "  ALB DNS: $ALB_DNS"

# ─────────────────────────────────────────────────────────────────────────────
# 4. ECS task definition (Fargate)
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [4/6] ECS Task Definition ==="

EXEC_ROLE_ARN="arn:aws:iam::$AWS_ACCOUNT:role/biomni-ecs-execution-role"
TASK_ROLE_ARN="arn:aws:iam::$AWS_ACCOUNT:role/biomni-task-role"

# Use :latest as the initial image — CD will update this to the SHA tag
INITIAL_IMAGE="$ECR_URI:latest"

aws ecs register-task-definition \
  --family "$TASK_FAMILY" \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu "2048" \
  --memory "8192" \
  --execution-role-arn "$EXEC_ROLE_ARN" \
  --task-role-arn "$TASK_ROLE_ARN" \
  --container-definitions "[
    {
      \"name\": \"$PROJECT\",
      \"image\": \"$INITIAL_IMAGE\",
      \"portMappings\": [
        {\"containerPort\": $CONTAINER_PORT, \"protocol\": \"tcp\"},
        {\"containerPort\": 7860, \"protocol\": \"tcp\"}
      ],
      \"environment\": [
        {\"name\": \"AWS_REGION\", \"value\": \"$AWS_REGION\"}
      ],
      \"secrets\": [
        {
          \"name\": \"ANTHROPIC_API_KEY\",
          \"valueFrom\": \"arn:aws:secretsmanager:$AWS_REGION:$AWS_ACCOUNT:secret:$PROJECT/anthropic-key\"
        }
      ],
      \"logConfiguration\": {
        \"logDriver\": \"awslogs\",
        \"options\": {
          \"awslogs-group\": \"/ecs/$PROJECT\",
          \"awslogs-region\": \"$AWS_REGION\",
          \"awslogs-stream-prefix\": \"ecs\"
        }
      },
      \"mountPoints\": [
        {
          \"sourceVolume\": \"efs-data\",
          \"containerPath\": \"/data\",
          \"readOnly\": false
        }
      ],
      \"essential\": true
    }
  ]" \
  --volumes "[
    {
      \"name\": \"efs-data\",
      \"efsVolumeConfiguration\": {
        \"fileSystemId\": \"$EFS_ID\",
        \"transitEncryption\": \"ENABLED\",
        \"authorizationConfig\": {
          \"accessPointId\": \"$(echo $EFS_AP | sed 's|.*access-points/||')\"
        }
      }
    }
  ]" \
  2>/dev/null || echo "  Task definition already registered (will update on next CD run)"

TASK_DEF_ARN=$(aws ecs describe-task-definition \
  --task-definition "$TASK_FAMILY" \
  --query "taskDefinition.taskDefinitionArn" --output text)
echo "  Task definition: $TASK_DEF_ARN"

# ─────────────────────────────────────────────────────────────────────────────
# 5. ECS Fargate service
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [5/6] ECS Service ==="

SERVICE_EXISTS=$(aws ecs describe-services \
  --cluster "$CLUSTER_NAME" \
  --services "$SERVICE_NAME" \
  --query "services[?status=='ACTIVE'].serviceName" \
  --output text 2>/dev/null || echo "")

if [ -z "$SERVICE_EXISTS" ]; then
  aws ecs create-service \
    --cluster "$CLUSTER_NAME" \
    --service-name "$SERVICE_NAME" \
    --task-definition "$TASK_FAMILY" \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={
      subnets=[$PRIV_SUB1,$PRIV_SUB2],
      securityGroups=[$APP_SG],
      assignPublicIp=DISABLED
    }" \
    --load-balancers "targetGroupArn=$TG_ARN,containerName=$PROJECT,containerPort=$CONTAINER_PORT" \
    --health-check-grace-period-seconds 120
  echo "  Created service: $SERVICE_NAME"
else
  echo "  Service already exists: $SERVICE_NAME"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6. Lambda function (container image)
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [6/6] Lambda Function ==="

LAMBDA_ROLE_ARN="arn:aws:iam::$AWS_ACCOUNT:role/biomni-lambda-role"

aws lambda create-function \
  --function-name "$LAMBDA_NAME" \
  --package-type Image \
  --code "ImageUri=$INITIAL_IMAGE" \
  --role "$LAMBDA_ROLE_ARN" \
  --timeout 900 \
  --memory-size 3008 \
  --environment "Variables={AWS_REGION=$AWS_REGION}" \
  --vpc-config "SubnetIds=$PRIV_SUB1,$PRIV_SUB2,SecurityGroupIds=$APP_SG" \
  2>/dev/null || echo "  Lambda already exists (will update on next CD run)"

echo "  Lambda: $LAMBDA_NAME"

# ─────────────────────────────────────────────────────────────────────────────
# Write updated env file
# ─────────────────────────────────────────────────────────────────────────────

cat >> "$ENV_FILE" <<ENV

# CD resources (added by setup-cd.sh)
OIDC_ROLE_ARN=$OIDC_ROLE_ARN
CLUSTER_NAME=$CLUSTER_NAME
SERVICE_NAME=$SERVICE_NAME
ALB_ARN=$ALB_ARN
ALB_DNS=$ALB_DNS
TG_ARN=$TG_ARN
LAMBDA_NAME=$LAMBDA_NAME
ENV
echo ""
echo "Updated infra/.env.generated"

# ─────────────────────────────────────────────────────────────────────────────
# Summary + GitHub secrets to set
# ─────────────────────────────────────────────────────────────────────────────
cat <<SUMMARY

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  CD Infrastructure Setup Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ECS Cluster   : $CLUSTER_NAME
  ECS Service   : $SERVICE_NAME
  ALB endpoint  : http://$ALB_DNS
  Lambda        : $LAMBDA_NAME

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ACTION REQUIRED: Set these GitHub secrets
  GitHub → Settings → Secrets and variables → Actions
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  AWS_ROLE_ARN          $OIDC_ROLE_ARN
  AWS_REGION            $AWS_REGION
  ECR_REPOSITORY        $PROJECT
  DEPLOY_TARGET         fargate
  ECS_CLUSTER           $CLUSTER_NAME
  ECS_SERVICE_FARGATE   $SERVICE_NAME
  LAMBDA_FUNCTION_NAME  $LAMBDA_NAME

  Optional (if using EKS):
  EKS_CLUSTER_NAME      $PROJECT

  For weekly benchmark:
  LANGCHAIN_API_KEY     ls__your_key_here

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Set the GitHub secrets above
  2. Make sure your Anthropic key is in Secrets Manager:
       aws secretsmanager put-secret-value \\
         --secret-id $PROJECT/anthropic-key \\
         --secret-string 'sk-ant-XXXXXXXX'
  3. Push any code change to main to trigger the first CD run:
       git commit --allow-empty -m "chore: trigger first CD"
       git push origin main
  4. Watch the pipeline: GitHub → Actions tab

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUMMARY

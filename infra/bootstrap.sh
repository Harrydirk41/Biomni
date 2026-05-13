#!/usr/bin/env bash
# infra/bootstrap.sh
# Creates all shared AWS infrastructure needed by every deployment tier.
# Run once before deploying to any tier.
# Usage:  bash infra/bootstrap.sh
# Requires: aws cli v2, jq

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — edit these before running
# ─────────────────────────────────────────────────────────────────────────────
export AWS_REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="biomni-infra"
VPC_CIDR="10.0.0.0/16"
PROJECT="biomni"

# Fetch account ID automatically
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo ">>> AWS Account : $AWS_ACCOUNT"
echo ">>> AWS Region  : $AWS_REGION"

# ─────────────────────────────────────────────────────────────────────────────
# 1. VPC  (skip if you already have one; export VPC_ID instead)
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [1/8] VPC ==="

VPC_ID=$(aws ec2 describe-vpcs \
  --filters "Name=tag:Name,Values=$PROJECT-vpc" \
  --query "Vpcs[0].VpcId" --output text 2>/dev/null || echo "None")

if [ "$VPC_ID" = "None" ] || [ -z "$VPC_ID" ]; then
  VPC_ID=$(aws ec2 create-vpc --cidr-block "$VPC_CIDR" \
    --query Vpc.VpcId --output text)
  aws ec2 create-tags --resources "$VPC_ID" \
    --tags Key=Name,Value="$PROJECT-vpc"
  aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-support
  aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames
  echo "Created VPC: $VPC_ID"
else
  echo "Using existing VPC: $VPC_ID"
fi

# Subnets (2 public + 2 private across 2 AZs)
AZ1="${AWS_REGION}a"
AZ2="${AWS_REGION}b"

PUB_SUB1=$(aws ec2 create-subnet --vpc-id "$VPC_ID" \
  --cidr-block "10.0.1.0/24" --availability-zone "$AZ1" \
  --query Subnet.SubnetId --output text 2>/dev/null || \
  aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=cidr-block,Values=10.0.1.0/24" \
    --query "Subnets[0].SubnetId" --output text)

PUB_SUB2=$(aws ec2 create-subnet --vpc-id "$VPC_ID" \
  --cidr-block "10.0.2.0/24" --availability-zone "$AZ2" \
  --query Subnet.SubnetId --output text 2>/dev/null || \
  aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=cidr-block,Values=10.0.2.0/24" \
    --query "Subnets[0].SubnetId" --output text)

PRIV_SUB1=$(aws ec2 create-subnet --vpc-id "$VPC_ID" \
  --cidr-block "10.0.11.0/24" --availability-zone "$AZ1" \
  --query Subnet.SubnetId --output text 2>/dev/null || \
  aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=cidr-block,Values=10.0.11.0/24" \
    --query "Subnets[0].SubnetId" --output text)

PRIV_SUB2=$(aws ec2 create-subnet --vpc-id "$VPC_ID" \
  --cidr-block "10.0.12.0/24" --availability-zone "$AZ2" \
  --query Subnet.SubnetId --output text 2>/dev/null || \
  aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=cidr-block,Values=10.0.12.0/24" \
    --query "Subnets[0].SubnetId" --output text)

echo "Public subnets : $PUB_SUB1  $PUB_SUB2"
echo "Private subnets: $PRIV_SUB1 $PRIV_SUB2"

# Internet Gateway
IGW_ID=$(aws ec2 describe-internet-gateways \
  --filters "Name=attachment.vpc-id,Values=$VPC_ID" \
  --query "InternetGateways[0].InternetGatewayId" --output text 2>/dev/null || echo "None")
if [ "$IGW_ID" = "None" ] || [ -z "$IGW_ID" ]; then
  IGW_ID=$(aws ec2 create-internet-gateway --query InternetGateway.InternetGatewayId --output text)
  aws ec2 attach-internet-gateway --internet-gateway-id "$IGW_ID" --vpc-id "$VPC_ID"
fi

# NAT Gateway (for private subnets outbound)
EIP_ALLOC=$(aws ec2 allocate-address --domain vpc --query AllocationId --output text)
NAT_ID=$(aws ec2 create-nat-gateway \
  --subnet-id "$PUB_SUB1" --allocation-id "$EIP_ALLOC" \
  --query NatGateway.NatGatewayId --output text)
echo "Waiting for NAT gateway..."
aws ec2 wait nat-gateway-available --nat-gateway-ids "$NAT_ID"

# ─────────────────────────────────────────────────────────────────────────────
# 2. Security Groups
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [2/8] Security Groups ==="

ALB_SG=$(aws ec2 create-security-group \
  --group-name "$PROJECT-alb-sg" --description "Biomni ALB" \
  --vpc-id "$VPC_ID" --query GroupId --output text 2>/dev/null || \
  aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$PROJECT-alb-sg" "Name=vpc-id,Values=$VPC_ID" \
    --query "SecurityGroups[0].GroupId" --output text)

APP_SG=$(aws ec2 create-security-group \
  --group-name "$PROJECT-app-sg" --description "Biomni App" \
  --vpc-id "$VPC_ID" --query GroupId --output text 2>/dev/null || \
  aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$PROJECT-app-sg" "Name=vpc-id,Values=$VPC_ID" \
    --query "SecurityGroups[0].GroupId" --output text)

EFS_SG=$(aws ec2 create-security-group \
  --group-name "$PROJECT-efs-sg" --description "Biomni EFS" \
  --vpc-id "$VPC_ID" --query GroupId --output text 2>/dev/null || \
  aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$PROJECT-efs-sg" "Name=vpc-id,Values=$VPC_ID" \
    --query "SecurityGroups[0].GroupId" --output text)

# ALB: allow 80 + 443 from internet
aws ec2 authorize-security-group-ingress --group-id "$ALB_SG" \
  --ip-permissions '[{"IpProtocol":"tcp","FromPort":80,"ToPort":80,"IpRanges":[{"CidrIp":"0.0.0.0/0"}]},{"IpProtocol":"tcp","FromPort":443,"ToPort":443,"IpRanges":[{"CidrIp":"0.0.0.0/0"}]}]' 2>/dev/null || true

# App: allow 8000 + 7860 from ALB SG
aws ec2 authorize-security-group-ingress --group-id "$APP_SG" \
  --ip-permissions "[{\"IpProtocol\":\"tcp\",\"FromPort\":8000,\"ToPort\":8000,\"UserIdGroupPairs\":[{\"GroupId\":\"$ALB_SG\"}]},{\"IpProtocol\":\"tcp\",\"FromPort\":7860,\"ToPort\":7860,\"UserIdGroupPairs\":[{\"GroupId\":\"$ALB_SG\"}]}]" 2>/dev/null || true

# EFS: allow NFS from app SG
aws ec2 authorize-security-group-ingress --group-id "$EFS_SG" \
  --ip-permissions "[{\"IpProtocol\":\"tcp\",\"FromPort\":2049,\"ToPort\":2049,\"UserIdGroupPairs\":[{\"GroupId\":\"$APP_SG\"}]}]" 2>/dev/null || true

echo "ALB SG : $ALB_SG"
echo "App SG : $APP_SG"
echo "EFS SG : $EFS_SG"

# ─────────────────────────────────────────────────────────────────────────────
# 3. EFS File System (shared data lake)
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [3/8] EFS File System ==="

EFS_ID=$(aws efs describe-file-systems \
  --query "FileSystems[?Tags[?Key=='Name' && Value=='$PROJECT-data']].FileSystemId" \
  --output text 2>/dev/null | head -1)

if [ -z "$EFS_ID" ] || [ "$EFS_ID" = "None" ]; then
  EFS_ID=$(aws efs create-file-system \
    --performance-mode generalPurpose \
    --throughput-mode elastic \
    --encrypted \
    --tags Key=Name,Value="$PROJECT-data" \
    --query FileSystemId --output text)
  aws efs wait file-system-available --file-system-id "$EFS_ID"
fi
echo "EFS ID: $EFS_ID"

# Mount targets in both private subnets
for SUBNET in "$PRIV_SUB1" "$PRIV_SUB2"; do
  aws efs create-mount-target \
    --file-system-id "$EFS_ID" \
    --subnet-id "$SUBNET" \
    --security-groups "$EFS_SG" 2>/dev/null || true
done

# Access point for ECS / Lambda
EFS_AP=$(aws efs create-access-point \
  --file-system-id "$EFS_ID" \
  --posix-user Uid=1000,Gid=1000 \
  --root-directory "Path=/biomni-data,CreationInfo={OwnerUid=1000,OwnerGid=1000,Permissions=755}" \
  --query AccessPoint.AccessPointArn --output text 2>/dev/null || \
  aws efs describe-access-points \
    --file-system-id "$EFS_ID" \
    --query "AccessPoints[0].AccessPointArn" --output text)
echo "EFS Access Point: $EFS_AP"

# ─────────────────────────────────────────────────────────────────────────────
# 4. ECR Repository
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [4/8] ECR Repository ==="

aws ecr create-repository --repository-name "$PROJECT" --region "$AWS_REGION" 2>/dev/null || true

aws ecr put-lifecycle-policy \
  --repository-name "$PROJECT" \
  --lifecycle-policy-text '{
    "rules":[{
      "rulePriority":1,
      "description":"Keep last 20 images",
      "selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":20},
      "action":{"type":"expire"}
    }]
  }' 2>/dev/null || true

ECR_URI="$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$PROJECT"
echo "ECR URI: $ECR_URI"

# ─────────────────────────────────────────────────────────────────────────────
# 5. Secrets Manager
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [5/8] Secrets Manager ==="

aws secretsmanager create-secret \
  --name "$PROJECT/anthropic-key" \
  --description "Anthropic API key for Biomni" \
  --secret-string "REPLACE_WITH_REAL_KEY" 2>/dev/null || true

echo "Secret created: $PROJECT/anthropic-key"
echo "ACTION REQUIRED: update the secret value with your real API key:"
echo "  aws secretsmanager put-secret-value \\"
echo "    --secret-id $PROJECT/anthropic-key \\"
echo "    --secret-string 'sk-ant-XXXXXXXX'"

# ─────────────────────────────────────────────────────────────────────────────
# 6. IAM Roles
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [6/8] IAM Roles ==="

# ECS Task Execution Role
aws iam create-role \
  --role-name biomni-ecs-execution-role \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},
    "Action":"sts:AssumeRole"}]
  }' 2>/dev/null || true

aws iam attach-role-policy \
  --role-name biomni-ecs-execution-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy 2>/dev/null || true

# ECS Task Role (runtime permissions)
aws iam create-role \
  --role-name biomni-task-role \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},
    "Action":"sts:AssumeRole"}]
  }' 2>/dev/null || true

aws iam put-role-policy \
  --role-name biomni-task-role \
  --policy-name biomni-task-policy \
  --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[
      {\"Effect\":\"Allow\",
       \"Action\":[\"bedrock:InvokeModel\",\"bedrock:InvokeModelWithResponseStream\"],
       \"Resource\":\"arn:aws:bedrock:$AWS_REGION::foundation-model/anthropic.claude-*\"},
      {\"Effect\":\"Allow\",
       \"Action\":[\"secretsmanager:GetSecretValue\"],
       \"Resource\":\"arn:aws:secretsmanager:$AWS_REGION:$AWS_ACCOUNT:secret:$PROJECT/*\"},
      {\"Effect\":\"Allow\",
       \"Action\":[\"elasticfilesystem:ClientMount\",\"elasticfilesystem:ClientWrite\",\"elasticfilesystem:ClientRootAccess\"],
       \"Resource\":\"*\"},
      {\"Effect\":\"Allow\",
       \"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",\"logs:PutLogEvents\"],
       \"Resource\":\"*\"},
      {\"Effect\":\"Allow\",
       \"Action\":[\"s3:GetObject\",\"s3:PutObject\",\"s3:ListBucket\"],
       \"Resource\":\"arn:aws:s3:::$PROJECT-*\"}
    ]
  }" 2>/dev/null || true

# Lambda Role
aws iam create-role \
  --role-name biomni-lambda-role \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},
    "Action":"sts:AssumeRole"}]
  }' 2>/dev/null || true

aws iam attach-role-policy \
  --role-name biomni-lambda-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole 2>/dev/null || true

aws iam put-role-policy \
  --role-name biomni-lambda-role \
  --policy-name biomni-lambda-policy \
  --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[
      {\"Effect\":\"Allow\",
       \"Action\":[\"bedrock:InvokeModel\",\"bedrock:InvokeModelWithResponseStream\"],
       \"Resource\":\"arn:aws:bedrock:$AWS_REGION::foundation-model/anthropic.claude-*\"},
      {\"Effect\":\"Allow\",
       \"Action\":[\"secretsmanager:GetSecretValue\"],
       \"Resource\":\"arn:aws:secretsmanager:$AWS_REGION:$AWS_ACCOUNT:secret:$PROJECT/*\"},
      {\"Effect\":\"Allow\",
       \"Action\":[\"elasticfilesystem:ClientMount\",\"elasticfilesystem:ClientWrite\"],
       \"Resource\":\"*\"},
      {\"Effect\":\"Allow\",
       \"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",\"logs:PutLogEvents\"],
       \"Resource\":\"*\"}
    ]
  }" 2>/dev/null || true

echo "IAM roles created."

# ─────────────────────────────────────────────────────────────────────────────
# 7. CloudWatch Log Groups
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [7/8] CloudWatch Log Groups ==="

for GROUP in /ecs/biomni /aws/lambda/biomni-agent /biomni/app; do
  aws logs create-log-group --log-group-name "$GROUP" --region "$AWS_REGION" 2>/dev/null || true
  aws logs put-retention-policy \
    --log-group-name "$GROUP" \
    --retention-in-days 30 2>/dev/null || true
done
echo "Log groups created."

# ─────────────────────────────────────────────────────────────────────────────
# 8. Output summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== [8/8] Summary ==="
cat <<SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Biomni Infrastructure Bootstrap Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  AWS Account   : $AWS_ACCOUNT
  AWS Region    : $AWS_REGION
  VPC ID        : $VPC_ID
  Public  Subnets : $PUB_SUB1, $PUB_SUB2
  Private Subnets : $PRIV_SUB1, $PRIV_SUB2
  ALB SG        : $ALB_SG
  App SG        : $APP_SG
  EFS SG        : $EFS_SG
  EFS ID        : $EFS_ID
  EFS AP        : $EFS_AP
  ECR URI       : $ECR_URI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  NEXT: update the secret with your real API key,
  then run the CI/CD pipeline to build & push the
  Docker image to ECR.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUMMARY

# Write IDs to a local env file for use by deploy scripts
cat > infra/.env.generated <<ENV
AWS_ACCOUNT=$AWS_ACCOUNT
AWS_REGION=$AWS_REGION
VPC_ID=$VPC_ID
PUB_SUB1=$PUB_SUB1
PUB_SUB2=$PUB_SUB2
PRIV_SUB1=$PRIV_SUB1
PRIV_SUB2=$PRIV_SUB2
ALB_SG=$ALB_SG
APP_SG=$APP_SG
EFS_SG=$EFS_SG
EFS_ID=$EFS_ID
EFS_AP=$EFS_AP
ECR_URI=$ECR_URI
ENV
echo "IDs saved to infra/.env.generated"

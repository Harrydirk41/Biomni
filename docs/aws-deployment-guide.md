# Biomni AWS Deployment Guide

End-to-end, step-by-step instructions for deploying the Biomni biomedical AI agent
on AWS across six compute tiers. Follow the sections in order — the **Prerequisites**
and **Shared Infrastructure** steps are required by every tier.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Shared Infrastructure](#2-shared-infrastructure)
3. [Build the Docker Image](#3-build-the-docker-image)
4. [Option A — AWS Lambda](#4-option-a--aws-lambda)
5. [Option B — AWS Bedrock (LLM backend)](#5-option-b--aws-bedrock-llm-backend)
6. [Option C — ECS Fargate](#6-option-c--ecs-fargate)
7. [Option D — ECS on EC2](#7-option-d--ecs-on-ec2)
8. [Option E — Amazon EKS](#8-option-e--amazon-eks)
9. [Option F — Raw EC2](#9-option-f--raw-ec2)
10. [CI/CD Pipeline](#10-cicd-pipeline)
11. [Decision Matrix](#11-decision-matrix)

---

## 1. Prerequisites

Install the following tools on your local machine before starting.

### 1.1 AWS CLI v2

```bash
# macOS
brew install awscli

# Linux
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install

# Verify
aws --version          # aws-cli/2.x.x
```

Configure credentials:

```bash
aws configure
# AWS Access Key ID     : AKIAIOSFODNN7EXAMPLE
# AWS Secret Access Key : wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
# Default region name   : us-east-1
# Default output format : json
```

### 1.2 Docker

```bash
# macOS — install Docker Desktop from https://docs.docker.com/desktop/install/mac/

# Linux (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
sudo usermod -aG docker $USER   # log out and back in after this

# Verify
docker --version               # Docker version 25.x.x
```

### 1.3 kubectl (for EKS only)

```bash
# macOS
brew install kubectl

# Linux
curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# Verify
kubectl version --client          # v1.29+
```

### 1.4 eksctl (for EKS only)

```bash
# macOS
brew tap weaveworks/tap
brew install weaveworks/tap/eksctl

# Linux
curl --silent --location \
  "https://github.com/eksctl-io/eksctl/releases/latest/download/eksctl_Linux_amd64.tar.gz" \
  | tar xz -C /tmp
sudo mv /tmp/eksctl /usr/local/bin

# Verify
eksctl version                    # 0.18x.x
```

### 1.5 helm (for EKS only)

```bash
# macOS
brew install helm

# Linux
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# Verify
helm version                      # v3.x.x
```

### 1.6 Clone the repo

```bash
git clone https://github.com/Harrydirk41/Biomni.git
cd Biomni
```

---

## 2. Shared Infrastructure

Run this once. It creates the VPC, subnets, security groups, EFS filesystem,
ECR repository, Secrets Manager entries, IAM roles, and CloudWatch log groups
that every deployment tier depends on.

```bash
# Set your region
export AWS_REGION=us-east-1

# Run the bootstrap script (takes ~5 minutes)
bash infra/bootstrap.sh
```

The script prints a summary and writes all resource IDs to `infra/.env.generated`.
Source that file in every subsequent step:

```bash
source infra/.env.generated
```

### 2.1 Store your real API key

```bash
# Replace with your actual Anthropic API key
aws secretsmanager put-secret-value \
  --secret-id biomni/anthropic-key \
  --secret-string "sk-ant-XXXXXXXXXXXXXXXX"
```

### 2.2 Enable Bedrock model access (required for Options B–E)

1. Open the [AWS Bedrock console](https://console.aws.amazon.com/bedrock/home).
2. Navigate to **Model access → Manage model access**.
3. Check **Anthropic → Claude** (Sonnet, Haiku) and click **Save changes**.
4. Wait until status shows **Access granted** (can take a few minutes).

---

## 3. Build the Docker Image

The same image is used by Lambda, ECS Fargate, ECS EC2, and EKS.
Raw EC2 does not use Docker.

### 3.1 Build locally

```bash
source infra/.env.generated

# Log in to ECR
aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin \
    $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com

# Build (first build takes 20–40 min due to conda env)
docker build \
  -f deploy/Dockerfile \
  -t $ECR_URI:latest \
  -t $ECR_URI:$(git rev-parse --short HEAD) \
  .

# Push
docker push $ECR_URI:latest
docker push $ECR_URI:$(git rev-parse --short HEAD)

echo "Image pushed: $ECR_URI:latest"
```

> **Tip:** Subsequent builds are fast because the conda env layer is cached.
> Only the `biomni/` source code layer re-builds on code changes.

### 3.2 Verify the image

```bash
docker run --rm \
  -e LLM_SOURCE=Anthropic \
  -e ANTHROPIC_API_KEY=sk-ant-test \
  -e BIOMNI_DATA_PATH=/tmp/data \
  -p 8000:8000 \
  $ECR_URI:latest \
  uvicorn app:app --host 0.0.0.0 --port 8000

# In another terminal
curl http://localhost:8000/health
# {"status":"ok","agent_ready":true}
```

---

## 4. Option A — AWS Lambda

Lambda is suited for short-burst tasks (≤ 15 minutes). The same container image
is deployed but the entry point is `lambda_handler.handler`.

### 4.1 Create the EFS Access Point for Lambda

Lambda requires an access point to mount EFS (already created by `bootstrap.sh`).

```bash
source infra/.env.generated

# Lambda needs its EFS access point ARN (printed by bootstrap.sh)
echo "EFS AP: $EFS_AP"
```

### 4.2 Create the Lambda function

```bash
source infra/.env.generated

aws lambda create-function \
  --function-name biomni-agent \
  --package-type Image \
  --code ImageUri="$ECR_URI:latest" \
  --role "arn:aws:iam::$AWS_ACCOUNT:role/biomni-lambda-role" \
  --timeout 900 \
  --memory-size 8192 \
  --ephemeral-storage '{"Size": 10240}' \
  --vpc-config "SubnetIds=$PRIV_SUB1,$PRIV_SUB2,SecurityGroupIds=$APP_SG" \
  --file-system-configs \
    "Arn=$EFS_AP,LocalMountPath=/mnt/efs" \
  --environment "Variables={
    LLM_SOURCE=Bedrock,
    BIOMNI_LLM=anthropic.claude-sonnet-4-5,
    BIOMNI_DATA_PATH=/mnt/efs/biomni-data
  }" \
  --region $AWS_REGION
```

Override the CMD to use the Lambda handler:

```bash
aws lambda update-function-configuration \
  --function-name biomni-agent \
  --image-config '{"command":["lambda_handler.handler"]}' \
  --region $AWS_REGION
```

### 4.3 Create a Function URL (quick HTTPS endpoint, no API Gateway)

```bash
aws lambda create-function-url-config \
  --function-name biomni-agent \
  --auth-type NONE \
  --region $AWS_REGION

# Or with IAM auth for production:
# --auth-type AWS_IAM
```

### 4.4 Test

```bash
LAMBDA_URL=$(aws lambda get-function-url-config \
  --function-name biomni-agent \
  --query FunctionUrl --output text)

curl -X POST "$LAMBDA_URL" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What tools are available for genomics analysis?"}'
```

### 4.5 Update Lambda when you push a new image

```bash
aws lambda update-function-code \
  --function-name biomni-agent \
  --image-uri "$ECR_URI:$(git rev-parse --short HEAD)" \
  --region $AWS_REGION

aws lambda wait function-updated --function-name biomni-agent
```

---

## 5. Option B — AWS Bedrock (LLM backend)

Bedrock replaces the Anthropic public API. No separate compute resource is created —
you configure it as the LLM provider on whichever tier you deploy.

### 5.1 How it works

Biomni's `llm.py` accepts `source="Bedrock"` which routes all LLM calls through
`langchain-aws → boto3 → Bedrock runtime`. The IAM role on your compute resource
(Lambda, ECS task, EKS pod, EC2 instance) is used for authentication — no API key needed.

### 5.2 Configuration (environment variables)

```bash
LLM_SOURCE=Bedrock
BIOMNI_LLM=anthropic.claude-sonnet-4-5
# No ANTHROPIC_API_KEY needed
```

### 5.3 IAM policy (already attached by bootstrap.sh)

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream"
  ],
  "Resource": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-*"
}
```

### 5.4 Available Bedrock model IDs for Biomni

```
anthropic.claude-sonnet-4-5          ← recommended default
anthropic.claude-3-5-sonnet-20241022-v2:0
anthropic.claude-3-haiku-20240307-v1:0   ← cheapest, fastest
meta.llama3-70b-instruct-v1:0            ← open-weight alternative
```

### 5.5 VPC Endpoint (optional — fully private traffic)

```bash
source infra/.env.generated

aws ec2 create-vpc-endpoint \
  --vpc-id "$VPC_ID" \
  --service-name "com.amazonaws.$AWS_REGION.bedrock-runtime" \
  --vpc-endpoint-type Interface \
  --subnet-ids "$PRIV_SUB1" "$PRIV_SUB2" \
  --security-group-ids "$APP_SG" \
  --private-dns-enabled \
  --region $AWS_REGION
```

With this endpoint, Bedrock calls never leave AWS — no internet gateway needed.

---

## 6. Option C — ECS Fargate

Fully managed containers. No EC2 to patch. Scales to zero between tasks.

### 6.1 Create ECS cluster

```bash
source infra/.env.generated

aws ecs create-cluster \
  --cluster-name biomni-cluster \
  --capacity-providers FARGATE FARGATE_SPOT \
  --default-capacity-provider-strategy \
    capacityProvider=FARGATE_SPOT,weight=4,base=0 \
    capacityProvider=FARGATE,weight=1,base=1 \
  --region $AWS_REGION
```

### 6.2 Create CloudWatch log group

```bash
aws logs create-log-group --log-group-name /ecs/biomni --region $AWS_REGION
```

### 6.3 Register task definition

Save the following as `deploy/fargate-task-def.json`, replacing placeholder values:

```json
{
  "family": "biomni",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "4096",
  "memory": "30720",
  "executionRoleArn": "arn:aws:iam::ACCOUNT:role/biomni-ecs-execution-role",
  "taskRoleArn":      "arn:aws:iam::ACCOUNT:role/biomni-task-role",
  "containerDefinitions": [
    {
      "name": "biomni",
      "image": "ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/biomni:latest",
      "portMappings": [
        {"containerPort": 8000, "protocol": "tcp", "name": "api"},
        {"containerPort": 7860, "protocol": "tcp", "name": "gradio"}
      ],
      "environment": [
        {"name": "BIOMNI_DATA_PATH", "value": "/data"},
        {"name": "LLM_SOURCE",       "value": "Bedrock"},
        {"name": "BIOMNI_LLM",       "value": "anthropic.claude-sonnet-4-5"}
      ],
      "secrets": [
        {
          "name": "ANTHROPIC_API_KEY",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:ACCOUNT:secret:biomni/anthropic-key"
        }
      ],
      "mountPoints": [
        {"containerPath": "/data", "sourceVolume": "biomni-data", "readOnly": false}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group":         "/ecs/biomni",
          "awslogs-region":        "us-east-1",
          "awslogs-stream-prefix": "fargate"
        }
      },
      "healthCheck": {
        "command":     ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
        "interval":    30,
        "timeout":     10,
        "retries":     3,
        "startPeriod": 90
      }
    }
  ],
  "volumes": [
    {
      "name": "biomni-data",
      "efsVolumeConfiguration": {
        "fileSystemId":       "fs-XXXXXXXX",
        "rootDirectory":      "/biomni-data",
        "transitEncryption":  "ENABLED",
        "authorizationConfig": {
          "accessPointId": "fsap-XXXXXXXX",
          "iam": "ENABLED"
        }
      }
    }
  ]
}
```

Substitute real values and register:

```bash
source infra/.env.generated

# Substitute placeholders
sed -i \
  -e "s/ACCOUNT/$AWS_ACCOUNT/g" \
  -e "s/us-east-1/$AWS_REGION/g" \
  -e "s/fs-XXXXXXXX/$EFS_ID/g" \
  -e "s/fsap-XXXXXXXX/$(echo $EFS_AP | grep -oP 'fsap-[a-z0-9]+')/g" \
  deploy/fargate-task-def.json

aws ecs register-task-definition \
  --cli-input-json file://deploy/fargate-task-def.json \
  --region $AWS_REGION
```

### 6.4 Create Application Load Balancer

```bash
source infra/.env.generated

# ALB
ALB_ARN=$(aws elbv2 create-load-balancer \
  --name biomni-alb \
  --subnets $PUB_SUB1 $PUB_SUB2 \
  --security-groups $ALB_SG \
  --query "LoadBalancers[0].LoadBalancerArn" --output text)

# Target group
TG_ARN=$(aws elbv2 create-target-group \
  --name biomni-tg \
  --protocol HTTP \
  --port 8000 \
  --vpc-id $VPC_ID \
  --target-type ip \
  --health-check-path /health \
  --health-check-interval-seconds 30 \
  --healthy-threshold-count 2 \
  --query "TargetGroups[0].TargetGroupArn" --output text)

# Listener (HTTP → redirect to HTTPS in production)
aws elbv2 create-listener \
  --load-balancer-arn $ALB_ARN \
  --protocol HTTP \
  --port 80 \
  --default-actions Type=forward,TargetGroupArn=$TG_ARN

ALB_DNS=$(aws elbv2 describe-load-balancers \
  --load-balancer-arns $ALB_ARN \
  --query "LoadBalancers[0].DNSName" --output text)
echo "ALB DNS: $ALB_DNS"
```

### 6.5 Create ECS service

```bash
source infra/.env.generated

aws ecs create-service \
  --cluster biomni-cluster \
  --service-name biomni-api \
  --task-definition biomni \
  --desired-count 2 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={
    subnets=[$PRIV_SUB1,$PRIV_SUB2],
    securityGroups=[$APP_SG],
    assignPublicIp=DISABLED
  }" \
  --load-balancers "targetGroupArn=$TG_ARN,containerName=biomni,containerPort=8000" \
  --health-check-grace-period-seconds 120 \
  --region $AWS_REGION
```

### 6.6 Auto-scaling

```bash
source infra/.env.generated

aws application-autoscaling register-scalable-target \
  --service-namespace ecs \
  --resource-id "service/biomni-cluster/biomni-api" \
  --scalable-dimension ecs:service:DesiredCount \
  --min-capacity 1 --max-capacity 20

aws application-autoscaling put-scaling-policy \
  --policy-name biomni-cpu-scaling \
  --service-namespace ecs \
  --resource-id "service/biomni-cluster/biomni-api" \
  --scalable-dimension ecs:service:DesiredCount \
  --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration '{
    "TargetValue": 70,
    "PredefinedMetricSpecification": {
      "PredefinedMetricType": "ECSServiceAverageCPUUtilization"
    },
    "ScaleInCooldown": 300,
    "ScaleOutCooldown": 60
  }'
```

### 6.7 Test

```bash
curl http://$ALB_DNS/health
curl -X POST http://$ALB_DNS/run \
  -H "Content-Type: application/json" \
  -d '{"prompt": "List available genomics tools"}'
```

---

## 7. Option D — ECS on EC2

Use EC2-backed ECS when you need GPUs, more than 30 GB RAM, or spot instances.

### 7.1 Create Launch Template for GPU nodes

```bash
source infra/.env.generated

# ECS-optimised GPU AMI
ECS_GPU_AMI=$(aws ssm get-parameter \
  --name /aws/service/ecs/optimized-ami/amazon-linux-2/gpu/recommended/image_id \
  --query Parameter.Value --output text --region $AWS_REGION)

USER_DATA=$(base64 -w0 <<EOF
#!/bin/bash
echo ECS_CLUSTER=biomni-cluster >> /etc/ecs/ecs.config
echo ECS_ENABLE_GPU_SUPPORT=true >> /etc/ecs/ecs.config
yum install -y amazon-efs-utils
mkdir -p /data
mount -t efs $EFS_ID:/ /data
echo '$EFS_ID:/ /data efs defaults,_netdev 0 0' >> /etc/fstab
EOF
)

aws ec2 create-launch-template \
  --launch-template-name biomni-gpu-lt \
  --version-description "Biomni GPU nodes" \
  --launch-template-data "{
    \"ImageId\": \"$ECS_GPU_AMI\",
    \"InstanceType\": \"g5.2xlarge\",
    \"IamInstanceProfile\": {\"Name\": \"ecsInstanceRole\"},
    \"SecurityGroupIds\": [\"$APP_SG\"],
    \"UserData\": \"$USER_DATA\",
    \"BlockDeviceMappings\": [{
      \"DeviceName\": \"/dev/xvda\",
      \"Ebs\": {\"VolumeSize\": 150, \"VolumeType\": \"gp3\", \"Iops\": 6000}
    }],
    \"TagSpecifications\": [{
      \"ResourceType\": \"instance\",
      \"Tags\": [{\"Key\": \"Name\", \"Value\": \"biomni-gpu-node\"}]
    }]
  }"
```

### 7.2 Create Auto Scaling Group (mixed on-demand + spot)

```bash
source infra/.env.generated

aws autoscaling create-auto-scaling-group \
  --auto-scaling-group-name biomni-gpu-asg \
  --min-size 0 --max-size 10 --desired-capacity 1 \
  --mixed-instances-policy '{
    "LaunchTemplate": {
      "LaunchTemplateSpecification": {
        "LaunchTemplateName": "biomni-gpu-lt",
        "Version": "$Latest"
      },
      "Overrides": [
        {"InstanceType": "g5.2xlarge"},
        {"InstanceType": "g4dn.2xlarge"},
        {"InstanceType": "p3.2xlarge"}
      ]
    },
    "InstancesDistribution": {
      "OnDemandBaseCapacity": 1,
      "OnDemandPercentageAboveBaseCapacity": 0,
      "SpotAllocationStrategy": "capacity-optimized"
    }
  }' \
  --vpc-zone-identifier "$PRIV_SUB1,$PRIV_SUB2" \
  --capacity-rebalance
```

### 7.3 Create ECS Capacity Provider

```bash
ASG_ARN=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names biomni-gpu-asg \
  --query "AutoScalingGroups[0].AutoScalingGroupARN" --output text)

aws ecs create-capacity-provider \
  --name biomni-gpu-cp \
  --auto-scaling-group-provider \
    "autoScalingGroupArn=$ASG_ARN,\
    managedScaling={status=ENABLED,targetCapacity=80,minimumScalingStepSize=1,maximumScalingStepSize=5},\
    managedTerminationProtection=ENABLED"

aws ecs put-cluster-capacity-providers \
  --cluster biomni-cluster \
  --capacity-providers biomni-gpu-cp FARGATE FARGATE_SPOT \
  --default-capacity-provider-strategy \
    capacityProvider=biomni-gpu-cp,weight=1,base=0
```

### 7.4 Register GPU task definition

Save as `deploy/ec2-task-def.json`:

```json
{
  "family": "biomni-gpu",
  "requiresCompatibilities": ["EC2"],
  "networkMode": "bridge",
  "containerDefinitions": [
    {
      "name": "sglang",
      "image": "lmsysorg/sglang:latest",
      "command": [
        "python", "-m", "sglang.launch_server",
        "--model-path", "Qwen/QwQ-32B",
        "--port", "30000", "--tp", "1"
      ],
      "resourceRequirements": [{"type": "GPU", "value": "1"}],
      "portMappings": [{"containerPort": 30000, "hostPort": 30000}],
      "mountPoints": [
        {"containerPath": "/root/.cache", "sourceVolume": "model-cache"}
      ],
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:30000/health || exit 1"],
        "interval": 30, "timeout": 10, "retries": 5, "startPeriod": 180
      }
    },
    {
      "name": "biomni",
      "image": "ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/biomni:latest",
      "dependsOn": [{"containerName": "sglang", "condition": "HEALTHY"}],
      "portMappings": [
        {"containerPort": 8000, "hostPort": 8000},
        {"containerPort": 7860, "hostPort": 7860}
      ],
      "environment": [
        {"name": "LLM_SOURCE",             "value": "Custom"},
        {"name": "BIOMNI_LLM",             "value": "QwQ-32B"},
        {"name": "BIOMNI_CUSTOM_BASE_URL", "value": "http://localhost:30000/v1"},
        {"name": "BIOMNI_DATA_PATH",       "value": "/data"}
      ],
      "mountPoints": [
        {"containerPath": "/data", "sourceVolume": "biomni-data"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/biomni",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ec2-gpu"
        }
      }
    }
  ],
  "volumes": [
    {
      "name": "biomni-data",
      "efsVolumeConfiguration": {"fileSystemId": "fs-XXXXXXXX"}
    },
    {
      "name": "model-cache",
      "host": {"sourcePath": "/tmp/model-cache"}
    }
  ]
}
```

```bash
source infra/.env.generated
sed -i \
  -e "s/ACCOUNT/$AWS_ACCOUNT/g" \
  -e "s/us-east-1/$AWS_REGION/g" \
  -e "s/fs-XXXXXXXX/$EFS_ID/g" \
  deploy/ec2-task-def.json

aws ecs register-task-definition \
  --cli-input-json file://deploy/ec2-task-def.json \
  --region $AWS_REGION
```

### 7.5 Create ECS EC2 service

```bash
aws ecs create-service \
  --cluster biomni-cluster \
  --service-name biomni-gpu-api \
  --task-definition biomni-gpu \
  --desired-count 1 \
  --launch-type EC2 \
  --capacity-provider-strategy \
    capacityProvider=biomni-gpu-cp,weight=1,base=1 \
  --region $AWS_REGION
```

---

## 8. Option E — Amazon EKS

Kubernetes-based deployment for production scale, multi-tenancy, and GitOps workflows.

### 8.1 Create EKS cluster

```bash
source infra/.env.generated

# This takes ~15 minutes
eksctl create cluster \
  --name biomni \
  --region $AWS_REGION \
  --version 1.29 \
  --with-oidc \
  --nodegroup-name system \
  --node-type t3.medium \
  --nodes 3 --nodes-min 2 --nodes-max 5 \
  --managed \
  --vpc-id $VPC_ID \
  --subnet-ids $PRIV_SUB1,$PRIV_SUB2,$PUB_SUB1,$PUB_SUB2
```

### 8.2 Add worker node groups

```bash
# CPU workers (spot, for API tasks)
eksctl create nodegroup \
  --cluster biomni \
  --name cpu-workers \
  --node-type r6i.4xlarge \
  --nodes 1 --nodes-min 0 --nodes-max 20 \
  --spot \
  --instance-types r6i.4xlarge,r6i.8xlarge,r5.4xlarge \
  --labels workload=cpu \
  --taints spot=true:NoSchedule \
  --region $AWS_REGION

# GPU workers (spot, for local Qwen-32B)
eksctl create nodegroup \
  --cluster biomni \
  --name gpu-workers \
  --node-type g5.2xlarge \
  --nodes 0 --nodes-min 0 --nodes-max 10 \
  --spot \
  --instance-types g5.2xlarge,g4dn.2xlarge \
  --labels workload=gpu \
  --taints nvidia.com/gpu=true:NoSchedule \
  --region $AWS_REGION
```

### 8.3 Install cluster add-ons

```bash
# Update kubeconfig
aws eks update-kubeconfig --name biomni --region $AWS_REGION

# 1. AWS Load Balancer Controller (for ALB Ingress)
helm repo add eks https://aws.github.io/eks-charts && helm repo update

eksctl create iamserviceaccount \
  --cluster biomni \
  --namespace kube-system \
  --name aws-load-balancer-controller \
  --attach-policy-arn arn:aws:iam::aws:policy/AWSLoadBalancerControllerIAMPolicy \
  --approve --region $AWS_REGION

helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=biomni \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller

# 2. EFS CSI Driver
helm repo add aws-efs-csi-driver \
  https://kubernetes-sigs.github.io/aws-efs-csi-driver/
helm install aws-efs-csi-driver aws-efs-csi-driver/aws-efs-csi-driver \
  -n kube-system

# 3. NVIDIA device plugin (for GPU nodes)
kubectl apply -f \
  https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.0/nvidia-device-plugin.yml

# 4. Cluster Autoscaler
helm repo add autoscaler https://kubernetes.github.io/autoscaler
helm install cluster-autoscaler autoscaler/cluster-autoscaler \
  --set autoDiscovery.clusterName=biomni \
  --set awsRegion=$AWS_REGION \
  -n kube-system
```

### 8.4 Update k8s manifests with your values

```bash
source infra/.env.generated

# Update StorageClass with your EFS ID
sed -i "s/fs-XXXXXXXX/$EFS_ID/" k8s/01-storage.yaml

# Update Deployment with your ECR URI
sed -i "s|ACCOUNT.dkr.ecr.us-east-1.amazonaws.com|$ECR_URI|g" \
  k8s/03-deployment-cpu.yaml

# Update Ingress with your domain and ACM cert (get cert ARN from ACM console)
CERT_ARN="arn:aws:acm:$AWS_REGION:$AWS_ACCOUNT:certificate/YOUR-CERT-ID"
sed -i \
  -e "s|arn:aws:acm:us-east-1:ACCOUNT:certificate/CERT_ID|$CERT_ARN|" \
  -e "s|biomni.example.com|YOUR_DOMAIN|g" \
  k8s/06-ingress.yaml
```

### 8.5 Create Kubernetes secret

```bash
ANTHROPIC_KEY=$(aws secretsmanager get-secret-value \
  --secret-id biomni/anthropic-key \
  --query SecretString --output text)

kubectl create secret generic biomni-secrets \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_KEY" \
  --namespace biomni \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 8.6 Apply all manifests

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-storage.yaml
kubectl apply -f k8s/02-secret.yaml
kubectl apply -f k8s/03-deployment-cpu.yaml
kubectl apply -f k8s/05-service.yaml
kubectl apply -f k8s/06-ingress.yaml
kubectl apply -f k8s/07-hpa.yaml

# Optional GPU deployment
kubectl apply -f k8s/04-deployment-gpu.yaml
```

### 8.7 Monitor rollout

```bash
kubectl rollout status deployment/biomni-api -n biomni
kubectl get pods -n biomni
kubectl get ingress -n biomni    # grab the ALB address
```

### 8.8 Test

```bash
ALB=$(kubectl get ingress biomni-ingress -n biomni \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

curl http://$ALB/health
curl -X POST http://$ALB/run \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Summarise CRISPR screen analysis methods"}'
```

---

## 9. Option F — Raw EC2

No Docker required. Full conda environment with R, Bash, all bioinformatics tools.

### 9.1 Launch instance

```bash
source infra/.env.generated

# Deep Learning AMI for GPU, Amazon Linux 2023 for CPU-only
DL_AMI=$(aws ssm get-parameter \
  --name /aws/service/deeplearning/ami/amazon-linux-2/pytorch/latest/image_id \
  --query Parameter.Value --output text --region $AWS_REGION)

INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "$DL_AMI" \
  --instance-type g5.2xlarge \
  --key-name YOUR_KEY_PAIR \
  --security-group-ids "$APP_SG" \
  --subnet-id "$PRIV_SUB1" \
  --iam-instance-profile Name=biomni-task-role \
  --block-device-mappings '[{
    "DeviceName":"/dev/xvda",
    "Ebs":{"VolumeSize":250,"VolumeType":"gp3","Iops":6000}
  }]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=biomni-research}]' \
  --query "Instances[0].InstanceId" --output text \
  --region $AWS_REGION)

echo "Instance ID: $INSTANCE_ID"
aws ec2 wait instance-running --instance-ids $INSTANCE_ID --region $AWS_REGION
```

### 9.2 Connect via SSM Session Manager (no SSH key needed)

```bash
aws ssm start-session \
  --target "$INSTANCE_ID" \
  --region $AWS_REGION
```

### 9.3 Install Biomni (run inside the instance)

```bash
# Mount EFS data lake
sudo yum install -y amazon-efs-utils
sudo mkdir -p /data
EFS_ID=fs-XXXXXXXX    # replace with your EFS ID from infra/.env.generated
sudo mount -t efs $EFS_ID:/ /data
echo "$EFS_ID:/ /data efs defaults,_netdev 0 0" | sudo tee -a /etc/fstab

# Install Miniconda if not present (Deep Learning AMI has conda already)
if ! command -v conda &>/dev/null; then
  wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
    -O /tmp/miniconda.sh
  bash /tmp/miniconda.sh -b -p /opt/conda
  echo 'export PATH=/opt/conda/bin:$PATH' >> ~/.bashrc
  source ~/.bashrc
fi

# Clone Biomni
git clone https://github.com/Harrydirk41/Biomni.git /opt/biomni
cd /opt/biomni

# Full environment (~1 hour, 30 GB)
conda env create -f biomni_env/environment.yml
conda activate biomni_e1
pip install -e .

# Fetch API key from Secrets Manager
export ANTHROPIC_API_KEY=$(aws secretsmanager get-secret-value \
  --secret-id biomni/anthropic-key \
  --query SecretString --output text)

# Write environment file for systemd
sudo tee /etc/biomni.env <<EOF
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
BIOMNI_DATA_PATH=/data
LLM_SOURCE=Bedrock
BIOMNI_LLM=anthropic.claude-sonnet-4-5
EOF
```

### 9.4 Create systemd service (auto-restart on reboot)

```bash
sudo tee /etc/systemd/system/biomni.service <<'EOF'
[Unit]
Description=Biomni AI Agent API
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/opt/biomni
EnvironmentFile=/etc/biomni.env
ExecStart=/opt/conda/envs/biomni_e1/bin/uvicorn \
  deploy.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable biomni
sudo systemctl start biomni
sudo systemctl status biomni
```

### 9.5 Serve Gradio UI via Nginx over HTTPS

```bash
sudo yum install -y nginx certbot python3-certbot-nginx

sudo tee /etc/nginx/conf.d/biomni.conf <<'EOF'
server {
    listen 80;
    server_name YOUR_DOMAIN;

    location /api/ {
        proxy_pass         http://127.0.0.1:8000/;
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
    }

    location / {
        proxy_pass         http://127.0.0.1:7860/;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_read_timeout 3600;
    }
}
EOF

sudo systemctl enable nginx && sudo systemctl start nginx
sudo certbot --nginx -d YOUR_DOMAIN
```

### 9.6 Launch Gradio UI as a background service

```bash
sudo tee /etc/systemd/system/biomni-ui.service <<'EOF'
[Unit]
Description=Biomni Gradio UI
After=biomni.service

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/opt/biomni
EnvironmentFile=/etc/biomni.env
ExecStart=/opt/conda/envs/biomni_e1/bin/python -c "
from biomni.agent import A1
import os
agent = A1(
    path=os.environ['BIOMNI_DATA_PATH'],
    llm=os.environ.get('BIOMNI_LLM','claude-sonnet-4-20250514'),
    source=os.environ.get('LLM_SOURCE','Anthropic')
)
agent.launch_gradio_demo(server_name='0.0.0.0', server_port=7860, share=False)
"
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable biomni-ui
sudo systemctl start biomni-ui
```

### 9.7 Cost-saving: auto-stop after idle hours

```bash
# Stop instance every night at 2 AM UTC
aws events put-rule \
  --name stop-biomni-nightly \
  --schedule-expression "cron(0 2 * * ? *)" \
  --state ENABLED \
  --region $AWS_REGION

# Create the stop target (requires Lambda or SSM automation)
# Simplest: add a cron on the instance itself
(crontab -l 2>/dev/null; echo "0 2 * * * sudo shutdown -h now") | crontab -
```

---

## 10. CI/CD Pipeline

The pipeline at `.github/workflows/ci-cd.yml` automates: test → build image →
push to ECR → deploy to your chosen tier(s). It uses GitHub OIDC to assume an
IAM role — no long-lived AWS credentials in GitHub secrets.

### 10.1 Create IAM role for GitHub Actions (OIDC)

```bash
source infra/.env.generated

GITHUB_ORG=Harrydirk41        # your GitHub org or username
GITHUB_REPO=Biomni             # your repository name

# Create OIDC provider for GitHub
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
  2>/dev/null || true

OIDC_ARN="arn:aws:iam::$AWS_ACCOUNT:oidc-provider/token.actions.githubusercontent.com"

# Trust policy
TRUST_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Federated": "$OIDC_ARN"},
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringLike": {
          "token.actions.githubusercontent.com:sub":
            "repo:$GITHUB_ORG/$GITHUB_REPO:*"
        },
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
JSON
)

aws iam create-role \
  --role-name biomni-github-actions-role \
  --assume-role-policy-document "$TRUST_POLICY"

# Attach permissions needed by CI/CD
aws iam attach-role-policy \
  --role-name biomni-github-actions-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser

aws iam put-role-policy \
  --role-name biomni-github-actions-role \
  --policy-name biomni-cicd-policy \
  --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[
      {\"Effect\":\"Allow\",
       \"Action\":[\"ecs:RegisterTaskDefinition\",\"ecs:UpdateService\",
         \"ecs:DescribeServices\",\"ecs:DescribeTaskDefinition\"],
       \"Resource\":\"*\"},
      {\"Effect\":\"Allow\",
       \"Action\":[\"lambda:UpdateFunctionCode\",\"lambda:PublishVersion\",
         \"lambda:UpdateAlias\",\"lambda:CreateAlias\",
         \"lambda:GetFunctionConfiguration\",\"lambda:WaitForFunctionUpdated\"],
       \"Resource\":\"arn:aws:lambda:$AWS_REGION:$AWS_ACCOUNT:function:biomni-*\"},
      {\"Effect\":\"Allow\",
       \"Action\":[\"eks:DescribeCluster\"],
       \"Resource\":\"arn:aws:eks:$AWS_REGION:$AWS_ACCOUNT:cluster/biomni\"},
      {\"Effect\":\"Allow\",
       \"Action\":[\"secretsmanager:GetSecretValue\"],
       \"Resource\":\"arn:aws:secretsmanager:$AWS_REGION:$AWS_ACCOUNT:secret:biomni/*\"},
      {\"Effect\":\"Allow\",
       \"Action\":[\"iam:PassRole\"],
       \"Resource\":[
         \"arn:aws:iam::$AWS_ACCOUNT:role/biomni-ecs-execution-role\",
         \"arn:aws:iam::$AWS_ACCOUNT:role/biomni-task-role\"
       ]}
    ]
  }"

ROLE_ARN="arn:aws:iam::$AWS_ACCOUNT:role/biomni-github-actions-role"
echo "GitHub Actions Role ARN: $ROLE_ARN"
```

### 10.2 Add GitHub repository secrets

In GitHub: **Settings → Secrets and variables → Actions → New repository secret**

| Secret Name | Value |
|---|---|
| `AWS_ROLE_ARN` | output of step 10.1 |
| `AWS_REGION` | `us-east-1` |
| `ECR_REPOSITORY` | `biomni` |
| `ECS_CLUSTER` | `biomni-cluster` |
| `ECS_SERVICE_FARGATE` | `biomni-api` |
| `ECS_SERVICE_EC2` | `biomni-gpu-api` |
| `LAMBDA_FUNCTION_NAME` | `biomni-agent` |
| `EKS_CLUSTER_NAME` | `biomni` |
| `DEPLOY_TARGET` | `fargate` (or `lambda`, `ec2`, `eks`, `all`) |

### 10.3 Push to trigger the pipeline

```bash
git add .
git commit -m "trigger deployment"
git push origin main
```

Watch progress at: **GitHub → Actions → Biomni CI/CD**

### 10.4 Pipeline stages

```
push to main
    │
    ▼
[test]        ── pytest (non-blocking until test suite exists)
    │
    ▼
[build]       ── docker build + push to ECR (layer-cached)
    │
    ├──▶ [deploy-lambda]    ── update function image + publish alias
    ├──▶ [deploy-fargate]   ── register new task def + update service
    ├──▶ [deploy-ecs-ec2]   ── register GPU task def + update service
    └──▶ [deploy-eks]       ── kubectl set image + wait for rollout
```

All deploy jobs run in parallel after the build completes.

---

## 11. Decision Matrix

| | Lambda | Bedrock | ECS Fargate | ECS EC2 | EKS | Raw EC2 |
|---|---|---|---|---|---|---|
| Docker needed | Yes | No | Yes | Yes | Yes | No |
| Kubernetes needed | No | No | No | No | Yes | No |
| Max task duration | 15 min | ∞ | ~1 hr | ∞ | ∞ | ∞ |
| GPU support | No | N/A | No | Yes | Yes | Yes |
| Full conda env | No | N/A | Yes | Yes | Yes | Yes |
| Scales to zero | Yes | Yes | Yes | Partial | Yes | No |
| Ops complexity | Low | None | Low | Medium | High | Low |
| Best for | Webhooks | All tiers | API service | GPU/batch | Production | Research |

### Recommended stacks

```
Single researcher:      Raw EC2 (g5.2xlarge)  +  Bedrock
Team API service:       ECS Fargate            +  Bedrock  +  CI/CD
GPU / local model:      ECS EC2 (g5 spot)      +  SGLang sidecar
Production platform:    EKS                    +  Bedrock  +  CI/CD  +  Spot nodes
Event-driven triggers:  Lambda                 +  Bedrock
```

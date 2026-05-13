# Biomni AWS Deployment Guide

This guide covers deploying the Biomni biomedical AI agent across six AWS compute tiers,
from fully managed serverless to bare-metal EC2. Each section explains the trade-offs and
provides ready-to-use configuration.

---

## Architecture Constraints

Before picking a tier, understand Biomni's hard requirements:

| Constraint | Detail |
|---|---|
| Environment size | ~11 GB data lake + 30 GB+ full conda env |
| Runtime | Python 3.11 + optional R + Bash execution |
| Task duration | Minutes to hours per research task |
| LLM calls | External API (Anthropic/OpenAI) or AWS Bedrock |
| GPU | Optional (for local Qwen-32B via SGLang) |
| Stateful | Data lake must persist across invocations |

---

## Option 1 — AWS Lambda (Serverless)

### When to use
Quick prototyping, low-traffic webhook triggers, pure API-call workloads with no local
model execution or R/Bash tools.

### Hard limits that apply to Biomni
- 15-minute max timeout — long research tasks will fail
- 10 GB container image — full conda env exceeds this; must strip to API-only subset
- 512 MB – 10 GB ephemeral `/tmp` — data lake won't fit in a single invocation
- No persistent filesystem — data lake must live in EFS or S3

### Architecture

```
API Gateway → Lambda (container) → Anthropic / Bedrock API
                    ↕
                  EFS mount (data lake)
                  S3 (conversation exports)
```

### Step 1 — Slim Dockerfile (API-only subset)

```dockerfile
# Dockerfile.lambda
FROM public.ecr.aws/lambda/python:3.11

# Only install API-call dependencies; skip R, Bash-exec tools
COPY biomni_env/requirements-api-only.txt .
RUN pip install --no-cache-dir -r requirements-api-only.txt

COPY biomni/ ${LAMBDA_TASK_ROOT}/biomni/
COPY lambda_handler.py ${LAMBDA_TASK_ROOT}/

CMD ["lambda_handler.handler"]
```

Create `requirements-api-only.txt` (strip scipy/scikit-learn/transformers for size):

```
langchain==0.3.*
langgraph==0.3.18
langchain-anthropic
langchain-openai
langchain-aws
pydantic
requests
beautifulsoup4
pyyaml
```

### Step 2 — Lambda Handler

```python
# lambda_handler.py
import json, os
from biomni.agent import A1

# Cold-start: agent is reused across warm invocations
_agent = None

def _get_agent():
    global _agent
    if _agent is None:
        _agent = A1(
            path=os.environ.get("BIOMNI_DATA_PATH", "/mnt/efs/biomni-data"),
            llm=os.environ.get("BIOMNI_LLM", "claude-sonnet-4-20250514"),
            source=os.environ.get("LLM_SOURCE", "Anthropic"),
        )
    return _agent

def handler(event, context):
    body = json.loads(event.get("body", "{}"))
    prompt = body.get("prompt", "")
    if not prompt:
        return {"statusCode": 400, "body": json.dumps({"error": "prompt required"})}

    agent = _get_agent()
    result = agent.go(prompt)
    return {
        "statusCode": 200,
        "body": json.dumps({"result": result}),
    }
```

### Step 3 — Build & Push to ECR

```bash
AWS_ACCOUNT=123456789012
AWS_REGION=us-east-1
REPO=biomni-lambda

aws ecr create-repository --repository-name $REPO --region $AWS_REGION

aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin \
    $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com

docker build -f Dockerfile.lambda -t $REPO .
docker tag $REPO:latest \
  $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest
docker push \
  $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest
```

### Step 4 — Create Function with EFS & Secrets

```bash
# Create EFS, mount target, and access point first (see AWS docs), then:

aws lambda create-function \
  --function-name biomni-agent \
  --package-type Image \
  --code ImageUri=$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest \
  --role arn:aws:iam::$AWS_ACCOUNT:role/biomni-lambda-role \
  --timeout 900 \
  --memory-size 8192 \
  --ephemeral-storage '{"Size": 10240}' \
  --file-system-configs \
    Arn=arn:aws:elasticfilesystem:$AWS_REGION:$AWS_ACCOUNT:access-point/fsap-XXXX,\
    LocalMountPath=/mnt/efs \
  --environment Variables="{
    ANTHROPIC_API_KEY=$(aws secretsmanager get-secret-value \
      --secret-id biomni/anthropic-key --query SecretString --output text),
    BIOMNI_DATA_PATH=/mnt/efs/biomni-data,
    LLM_SOURCE=Anthropic
  }"
```

### IAM policy for the Lambda role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:*:*:secret:biomni/*" },
    { "Effect": "Allow",
      "Action": ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"],
      "Resource": "*" },
    { "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "*" }
  ]
}
```

### Verdict
**Feasible only for API-call-only tasks.** The 15-minute timeout and image size cap make
it unsuitable for long-running genomics/proteomics analyses. Use ECS Fargate instead.

---

## Option 2 — AWS Bedrock (Managed Inference)

### What this is
Bedrock is not a compute host — it is a **managed LLM inference API**. You deploy Biomni's
orchestration layer on any of the other tiers and point its LLM calls at Bedrock instead
of the Anthropic public API. This eliminates API key management and keeps all traffic
inside the AWS network.

### When to use
Any tier where you want VPC-private LLM calls, IAM-based auth, or pay-per-token billing
without a separate Anthropic account.

### Architecture

```
Biomni Agent (Lambda / ECS / EKS / EC2)
        │
        │  IAM role (no API key needed)
        ▼
  AWS Bedrock API (Claude / Titan / Llama)
        │
        ▼
  VPC Endpoint (optional, for fully private traffic)
```

### Step 1 — Enable Claude on Bedrock

In the AWS console: **Bedrock → Model access → Manage model access** → enable
`Anthropic Claude` models in your region.

### Step 2 — Configure Biomni to use Bedrock

```python
from biomni.agent import A1

# Biomni's llm.py supports "Bedrock" as a source via langchain-aws
agent = A1(
    path="./data",
    llm="anthropic.claude-sonnet-4-5",   # Bedrock model ID
    source="Bedrock",
    # No api_key needed — uses EC2/ECS/Lambda IAM role credentials
)
```

Or via environment variables:

```bash
export LLM_SOURCE=Bedrock
export BIOMNI_LLM=anthropic.claude-sonnet-4-5
# AWS credentials come from the instance/task IAM role automatically
```

### Step 3 — IAM policy for the compute role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-*"
      ]
    }
  ]
}
```

Attach this policy to the IAM role used by your Lambda function, ECS task, EKS node,
or EC2 instance.

### Step 4 — (Optional) Bedrock VPC Endpoint

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-XXXXXXXX \
  --service-name com.amazonaws.us-east-1.bedrock-runtime \
  --vpc-endpoint-type Interface \
  --subnet-ids subnet-XXXXXXXX \
  --security-group-ids sg-XXXXXXXX \
  --private-dns-enabled
```

This routes all Bedrock traffic over the AWS private network with no internet gateway.

### Available models on Bedrock for Biomni

| Bedrock Model ID | Equivalent |
|---|---|
| `anthropic.claude-sonnet-4-5` | Claude Sonnet 4.5 |
| `anthropic.claude-3-5-sonnet-20241022-v2:0` | Claude 3.5 Sonnet |
| `anthropic.claude-3-haiku-20240307-v1:0` | Claude 3 Haiku (cheap/fast) |
| `meta.llama3-70b-instruct-v1:0` | Open-weight alternative |

### Verdict
**Use Bedrock on every other tier** (ECS/EKS/EC2) to avoid rotating Anthropic API keys
and to keep LLM traffic inside the AWS network.

---

## Option 3 — ECS Fargate (Serverless Containers)

### When to use
Production API service, no desire to manage EC2. Tasks up to 4 vCPU / 30 GB RAM.
Good for moderate-length research tasks (up to ~1 hour).

### Architecture

```
ALB (HTTPS)
  │
  ▼
ECS Fargate Task (biomni container)
  ├── FastAPI wrapper (port 8000)
  ├── Gradio UI    (port 7860)
  │
  ├── EFS mount    (/data  — 11 GB data lake, persists)
  └── Secrets Manager (ANTHROPIC_API_KEY)
```

### Step 1 — Dockerfile

```dockerfile
# Dockerfile
FROM continuumio/miniconda3:24.1.2-0

WORKDIR /app

# Install conda environment (heavy layer — cache aggressively)
COPY biomni_env/environment.yml .
RUN conda env create -f environment.yml && conda clean -afy

# Install Biomni
COPY . .
RUN conda run -n biomni_e1 pip install -e . --no-deps

# FastAPI wrapper
COPY deploy/app.py /app/app.py

ENV CONDA_DEFAULT_ENV=biomni_e1
ENV PATH=/opt/conda/envs/biomni_e1/bin:$PATH

EXPOSE 8000 7860

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Step 2 — FastAPI wrapper

```python
# deploy/app.py
import asyncio, os
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from biomni.agent import A1

agent: A1 = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    agent = A1(
        path=os.environ["BIOMNI_DATA_PATH"],
        llm=os.environ.get("BIOMNI_LLM", "claude-sonnet-4-20250514"),
        source=os.environ.get("LLM_SOURCE", "Anthropic"),
    )
    yield

app = FastAPI(lifespan=lifespan)

class PromptRequest(BaseModel):
    prompt: str

@app.post("/run")
async def run_agent(req: PromptRequest):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, agent.go, req.prompt)
    return {"result": result}

@app.get("/stream")
async def stream_agent(prompt: str):
    async def gen():
        for chunk in agent.go_stream(prompt):
            yield f"data: {chunk}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")

@app.get("/health")
def health():
    return {"status": "ok"}
```

### Step 3 — Task Definition (JSON)

```json
{
  "family": "biomni",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "4096",
  "memory": "30720",
  "executionRoleArn": "arn:aws:iam::ACCOUNT:role/ecsTaskExecutionRole",
  "taskRoleArn":      "arn:aws:iam::ACCOUNT:role/biomni-task-role",
  "containerDefinitions": [
    {
      "name": "biomni",
      "image": "ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/biomni:latest",
      "portMappings": [
        {"containerPort": 8000, "protocol": "tcp"},
        {"containerPort": 7860, "protocol": "tcp"}
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
        {"containerPath": "/data", "sourceVolume": "biomni-data"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group":  "/ecs/biomni",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ],
  "volumes": [
    {
      "name": "biomni-data",
      "efsVolumeConfiguration": {
        "fileSystemId": "fs-XXXXXXXX",
        "rootDirectory":   "/biomni-data",
        "transitEncryption": "ENABLED"
      }
    }
  ]
}
```

### Step 4 — Register & Run

```bash
aws ecs register-task-definition --cli-input-json file://task-def.json

aws ecs create-service \
  --cluster biomni-cluster \
  --service-name biomni-api \
  --task-definition biomni \
  --desired-count 2 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={
    subnets=[subnet-XXXX,subnet-YYYY],
    securityGroups=[sg-XXXX],
    assignPublicIp=DISABLED
  }" \
  --load-balancers "targetGroupArn=arn:aws:elasticloadbalancing:...,
    containerName=biomni,containerPort=8000"
```

### Auto-scaling by SQS queue depth

```bash
aws application-autoscaling register-scalable-target \
  --service-namespace ecs \
  --resource-id service/biomni-cluster/biomni-api \
  --scalable-dimension ecs:service:DesiredCount \
  --min-capacity 1 --max-capacity 10

aws application-autoscaling put-scaling-policy \
  --policy-name biomni-queue-scaling \
  --service-namespace ecs \
  --resource-id service/biomni-cluster/biomni-api \
  --scalable-dimension ecs:service:DesiredCount \
  --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration '{
    "TargetValue": 5,
    "CustomizedMetricSpecification": {
      "MetricName": "ApproximateNumberOfMessagesVisible",
      "Namespace": "AWS/SQS",
      "Dimensions": [{"Name":"QueueName","Value":"biomni-jobs"}],
      "Statistic": "Average"
    }
  }'
```

### Verdict
**Best managed option.** No EC2 to patch; scales to zero; EFS persists the data lake.
Limit: no GPU support, 30 GB RAM ceiling.

---

## Option 4 — ECS on EC2 (EC2-backed Cluster)

### When to use
Need GPU for local Qwen-32B model, more than 30 GB RAM, or spot instances for cost
savings. You manage the EC2 fleet; ECS handles scheduling.

### Architecture

```
ECS Cluster (EC2 launch type)
  ├── g5.2xlarge (GPU tasks — Qwen-32B via SGLang)
  ├── r6i.4xlarge (CPU-only — API-call tasks)
  └── Spot fleet (batch workloads)
        │
        ├── EFS /data  (shared data lake across all tasks)
        └── ECR image  (same image as Fargate)
```

### Step 1 — Launch Configuration for GPU Nodes

```bash
# ECS-optimized GPU AMI
ECS_GPU_AMI=$(aws ssm get-parameter \
  --name /aws/service/ecs/optimized-ami/amazon-linux-2/gpu/recommended/image_id \
  --query Parameter.Value --output text)

aws ec2 create-launch-template \
  --launch-template-name biomni-gpu-lt \
  --version-description "Biomni GPU nodes" \
  --launch-template-data "{
    \"ImageId\": \"$ECS_GPU_AMI\",
    \"InstanceType\": \"g5.2xlarge\",
    \"IamInstanceProfile\": {\"Name\": \"ecsInstanceRole\"},
    \"UserData\": \"$(base64 -w0 <<'EOF'
#!/bin/bash
echo ECS_CLUSTER=biomni-cluster >> /etc/ecs/ecs.config
echo ECS_ENABLE_GPU_SUPPORT=true >> /etc/ecs/ecs.config
# Mount EFS
yum install -y amazon-efs-utils
mkdir -p /data
mount -t efs fs-XXXXXXXX:/ /data
echo 'fs-XXXXXXXX:/ /data efs defaults,_netdev 0 0' >> /etc/fstab
EOF
)\",
    \"BlockDeviceMappings\": [{
      \"DeviceName\": \"/dev/xvda\",
      \"Ebs\": {\"VolumeSize\": 100, \"VolumeType\": \"gp3\"}
    }]
  }"
```

### Step 2 — Create Mixed-Instance Auto Scaling Group

```bash
aws autoscaling create-auto-scaling-group \
  --auto-scaling-group-name biomni-ec2-asg \
  --min-size 1 --max-size 20 --desired-capacity 2 \
  --mixed-instances-policy '{
    "LaunchTemplate": {
      "LaunchTemplateSpecification": {
        "LaunchTemplateName": "biomni-gpu-lt", "Version": "$Latest"
      },
      "Overrides": [
        {"InstanceType": "g5.2xlarge"},
        {"InstanceType": "g4dn.2xlarge"},
        {"InstanceType": "p3.2xlarge"}
      ]
    },
    "InstancesDistribution": {
      "OnDemandBaseCapacity": 1,
      "OnDemandPercentageAboveBaseCapacity": 30,
      "SpotAllocationStrategy": "capacity-optimized"
    }
  }' \
  --vpc-zone-identifier "subnet-XXXX,subnet-YYYY" \
  --capacity-rebalance
```

### Step 3 — GPU Task Definition

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
        "--port", "30000",
        "--tp", "1"
      ],
      "resourceRequirements": [
        {"type": "GPU", "value": "1"}
      ],
      "mountPoints": [
        {"containerPath": "/root/.cache", "sourceVolume": "model-cache"}
      ],
      "portMappings": [{"containerPort": 30000, "hostPort": 30000}]
    },
    {
      "name": "biomni",
      "image": "ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/biomni:latest",
      "dependsOn": [{"containerName": "sglang", "condition": "HEALTHY"}],
      "environment": [
        {"name": "LLM_SOURCE",          "value": "Custom"},
        {"name": "BIOMNI_LLM",          "value": "QwQ-32B"},
        {"name": "BIOMNI_CUSTOM_BASE_URL","value": "http://localhost:30000/v1"},
        {"name": "BIOMNI_DATA_PATH",    "value": "/data"}
      ],
      "mountPoints": [
        {"containerPath": "/data", "sourceVolume": "biomni-data"}
      ]
    }
  ],
  "volumes": [
    {"name": "biomni-data",
     "efsVolumeConfiguration": {"fileSystemId": "fs-XXXXXXXX"}},
    {"name": "model-cache",
     "host": {"sourcePath": "/tmp/model-cache"}}
  ]
}
```

### Step 4 — Capacity Provider

```bash
aws ecs create-capacity-provider \
  --name biomni-gpu-cp \
  --auto-scaling-group-provider \
    autoScalingGroupArn=arn:aws:autoscaling:us-east-1:ACCOUNT:autoScalingGroup:...,\
    managedScaling='{status=ENABLED,targetCapacity=80}',\
    managedTerminationProtection=ENABLED

aws ecs put-cluster-capacity-providers \
  --cluster biomni-cluster \
  --capacity-providers biomni-gpu-cp FARGATE FARGATE_SPOT \
  --default-capacity-provider-strategy \
    capacityProvider=biomni-gpu-cp,weight=1,base=0
```

### Verdict
**Best for GPU / high-memory workloads.** Spot instances cut cost by 60–80%. More
operational overhead than Fargate (you patch AMIs, manage disk space).

---

## Option 5 — Amazon EKS (Kubernetes)

### When to use
Multi-tenant platform, complex routing, GitOps workflows, or you already run Kubernetes
in production. Best for teams with Kubernetes expertise.

### Architecture

```
EKS Cluster
  ├── System node group   (t3.medium × 3)
  ├── CPU node group      (r6i.4xlarge, spot)
  └── GPU node group      (g5.2xlarge, spot)
        │
  Namespace: biomni
  ├── Deployment: biomni-api    (3 replicas, CPU)
  ├── Deployment: sglang        (1 replica, GPU)
  ├── Service: biomni-svc       (ClusterIP)
  ├── Ingress (ALB)
  ├── HPA (CPU + custom SQS metric)
  └── PersistentVolumeClaim (EFS CSI)
```

### Step 1 — Create EKS Cluster

```bash
eksctl create cluster \
  --name biomni \
  --region us-east-1 \
  --version 1.29 \
  --with-oidc \
  --nodegroup-name system \
  --node-type t3.medium \
  --nodes 3 \
  --nodes-min 2 \
  --nodes-max 5 \
  --managed

# CPU worker node group
eksctl create nodegroup \
  --cluster biomni \
  --name cpu-workers \
  --node-type r6i.4xlarge \
  --nodes 1 --nodes-min 0 --nodes-max 20 \
  --spot \
  --instance-types r6i.4xlarge,r6i.8xlarge,r5.4xlarge \
  --labels workload=cpu

# GPU node group
eksctl create nodegroup \
  --cluster biomni \
  --name gpu-workers \
  --node-type g5.2xlarge \
  --nodes 0 --nodes-min 0 --nodes-max 10 \
  --spot \
  --instance-types g5.2xlarge,g4dn.2xlarge \
  --labels workload=gpu \
  --node-labels="nvidia.com/gpu=true"
```

### Step 2 — Install Add-ons

```bash
# AWS Load Balancer Controller
helm repo add eks https://aws.github.io/eks-charts
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=biomni \
  --set serviceAccount.create=true \
  --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"=\
    arn:aws:iam::ACCOUNT:role/AmazonEKSLoadBalancerControllerRole

# EFS CSI Driver
helm repo add aws-efs-csi-driver \
  https://kubernetes-sigs.github.io/aws-efs-csi-driver/
helm install aws-efs-csi-driver aws-efs-csi-driver/aws-efs-csi-driver \
  -n kube-system

# NVIDIA device plugin
kubectl apply -f \
  https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.0/nvidia-device-plugin.yml

# Cluster Autoscaler
helm install cluster-autoscaler autoscaler/cluster-autoscaler \
  --set autoDiscovery.clusterName=biomni \
  --set awsRegion=us-east-1
```

### Step 3 — Kubernetes Manifests

```yaml
# k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: biomni
---
# k8s/secret.yaml  (use External Secrets Operator in production)
apiVersion: v1
kind: Secret
metadata:
  name: biomni-secrets
  namespace: biomni
type: Opaque
stringData:
  ANTHROPIC_API_KEY: "sk-ant-..."
---
# k8s/pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: biomni-data
  namespace: biomni
spec:
  accessModes: [ReadWriteMany]
  storageClassName: efs-sc
  resources:
    requests:
      storage: 50Gi
---
# k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: biomni-api
  namespace: biomni
spec:
  replicas: 2
  selector:
    matchLabels:
      app: biomni-api
  template:
    metadata:
      labels:
        app: biomni-api
    spec:
      nodeSelector:
        workload: cpu
      tolerations:
        - key: "spot"
          operator: "Equal"
          value: "true"
          effect: "NoSchedule"
      containers:
        - name: biomni
          image: ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/biomni:latest
          ports:
            - containerPort: 8000
          env:
            - name: BIOMNI_DATA_PATH
              value: /data
            - name: LLM_SOURCE
              value: Bedrock
            - name: BIOMNI_LLM
              value: anthropic.claude-sonnet-4-5
          envFrom:
            - secretRef:
                name: biomni-secrets
          volumeMounts:
            - name: data
              mountPath: /data
          resources:
            requests:
              cpu: "2"
              memory: "16Gi"
            limits:
              cpu: "4"
              memory: "30Gi"
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 60
            periodSeconds: 10
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: biomni-data
---
# k8s/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: biomni-svc
  namespace: biomni
spec:
  selector:
    app: biomni-api
  ports:
    - port: 80
      targetPort: 8000
---
# k8s/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: biomni-ingress
  namespace: biomni
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/certificate-arn: arn:aws:acm:...
spec:
  rules:
    - host: biomni.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: biomni-svc
                port:
                  number: 80
---
# k8s/hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: biomni-hpa
  namespace: biomni
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: biomni-api
  minReplicas: 1
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### Step 4 — GPU Deployment for Qwen-32B

```yaml
# k8s/sglang-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sglang
  namespace: biomni
spec:
  replicas: 1
  selector:
    matchLabels:
      app: sglang
  template:
    metadata:
      labels:
        app: sglang
    spec:
      nodeSelector:
        workload: gpu
      containers:
        - name: sglang
          image: lmsysorg/sglang:latest
          args:
            - python
            - -m
            - sglang.launch_server
            - --model-path
            - Qwen/QwQ-32B
            - --port
            - "30000"
          resources:
            limits:
              nvidia.com/gpu: "1"
              memory: "60Gi"
          ports:
            - containerPort: 30000
```

### Step 5 — Deploy

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/ingress.yaml
kubectl apply -f k8s/hpa.yaml

# Watch rollout
kubectl rollout status deployment/biomni-api -n biomni
```

### Verdict
**Most scalable and production-hardened.** GitOps-friendly, supports blue/green
deployments, multi-tenant isolation, and advanced autoscaling. High operational
complexity; requires Kubernetes expertise.

---

## Option 6 — Raw EC2

### When to use
Full-environment installation (30 GB+ conda, R, all bioinformatics tools), GPU
for local model, or one-time research compute. Simplest to set up for a single
researcher.

### Architecture

```
EC2 Instance (GPU optional)
  ├── Biomni full conda environment (/opt/conda)
  ├── Data lake                     (/data)
  ├── Gradio UI                     (port 7860, HTTPS via Nginx)
  ├── FastAPI                       (port 8000, optional)
  └── systemd service               (auto-restart)
```

### Recommended instance types

| Use Case | Instance | vCPU | RAM | GPU | Cost/hr |
|---|---|---|---|---|---|
| API-call only | r6i.2xlarge | 8 | 64 GB | — | ~$0.50 |
| Full bioinformatics | r6i.8xlarge | 32 | 256 GB | — | ~$2.00 |
| Local Qwen-32B | g5.12xlarge | 48 | 192 GB | 4×A10G | ~$5.67 |
| Max performance | p4d.24xlarge | 96 | 1152 GB | 8×A100 | ~$32 |

### Step 1 — Launch EC2 Instance

```bash
# Use Deep Learning AMI for GPU, Amazon Linux 2023 for CPU-only
DL_AMI=$(aws ssm get-parameter \
  --name /aws/service/deeplearning/ami/amazon-linux-2/pytorch/latest/image_id \
  --query Parameter.Value --output text)

aws ec2 run-instances \
  --image-id $DL_AMI \
  --instance-type g5.2xlarge \
  --key-name my-key-pair \
  --security-group-ids sg-XXXXXXXX \
  --subnet-id subnet-XXXXXXXX \
  --iam-instance-profile Name=biomni-ec2-role \
  --block-device-mappings '[{
    "DeviceName": "/dev/xvda",
    "Ebs": {"VolumeSize": 200, "VolumeType": "gp3", "Iops": 6000}
  }]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=biomni}]'
```

### Step 2 — Bootstrap Script

```bash
#!/bin/bash
# Run as ec2-user after instance starts

# System packages
sudo yum update -y
sudo yum install -y git wget curl nginx amazon-efs-utils

# Mount EFS for shared data lake (optional; use local SSD for single instance)
sudo mkdir -p /data
sudo mount -t efs fs-XXXXXXXX:/ /data
echo 'fs-XXXXXXXX:/ /data efs defaults,_netdev 0 0' | sudo tee -a /etc/fstab

# Install Miniconda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
bash /tmp/miniconda.sh -b -p /opt/conda
echo 'export PATH=/opt/conda/bin:$PATH' >> ~/.bashrc
source ~/.bashrc

# Clone & install Biomni
git clone https://github.com/your-org/Biomni.git /opt/biomni
cd /opt/biomni

# Full environment (30+ GB, ~1 hour on first run)
conda env create -f biomni_env/environment.yml
conda activate biomni_e1
pip install -e .

# Or: Quick install from PyPI
# conda create -n biomni_e1 python=3.11 -y
# conda activate biomni_e1
# pip install biomni

# Set secrets from SSM Parameter Store
export ANTHROPIC_API_KEY=$(aws ssm get-parameter \
  --name /biomni/anthropic-key --with-decryption \
  --query Parameter.Value --output text)
export BIOMNI_DATA_PATH=/data
echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" | sudo tee /etc/biomni.env
echo "BIOMNI_DATA_PATH=$BIOMNI_DATA_PATH"  | sudo tee -a /etc/biomni.env
```

### Step 3 — Systemd Service

```ini
# /etc/systemd/system/biomni.service
[Unit]
Description=Biomni AI Agent
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/opt/biomni
EnvironmentFile=/etc/biomni.env
ExecStart=/opt/conda/envs/biomni_e1/bin/python -m uvicorn \
  deploy.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable biomni
sudo systemctl start biomni
sudo systemctl status biomni
```

### Step 4 — Gradio UI via Nginx (HTTPS)

```nginx
# /etc/nginx/conf.d/biomni.conf
server {
    listen 443 ssl;
    server_name biomni.example.com;

    ssl_certificate     /etc/letsencrypt/live/biomni.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/biomni.example.com/privkey.pem;

    # FastAPI backend
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
    }

    # Gradio UI
    location / {
        proxy_pass http://127.0.0.1:7860/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600;
    }
}

server {
    listen 80;
    server_name biomni.example.com;
    return 301 https://$host$request_uri;
}
```

```bash
# TLS certificate
sudo certbot --nginx -d biomni.example.com
sudo systemctl restart nginx
```

### Step 5 — Launch Gradio UI (Researcher Mode)

```python
# /opt/biomni/launch_ui.py
from biomni.agent import A1

agent = A1(
    path="/data",
    llm="claude-sonnet-4-20250514",
    source="Anthropic",
)
agent.launch_gradio_demo(
    server_name="0.0.0.0",
    server_port=7860,
    share=False,          # set True for temporary public URL
)
```

```bash
# Add as separate systemd unit for the UI
conda run -n biomni_e1 python /opt/biomni/launch_ui.py
```

### Step 6 — Scheduled Shutdown (cost saving)

```bash
# Auto-stop after 8 hours of idle
aws events put-rule \
  --name stop-biomni-nightly \
  --schedule-expression "cron(0 2 * * ? *)" \
  --state ENABLED

# Or use instance scheduler:
# aws ec2 stop-instances --instance-ids i-XXXXXXXX
```

### Verdict
**Simplest for full-environment research.** No containerization overhead, full R/Bash
support, can install any bioinformatics tool. Not scalable — single instance, manual
management.

---

## Decision Matrix

| | Lambda | Bedrock | ECS Fargate | ECS EC2 | EKS | Raw EC2 |
|---|---|---|---|---|---|---|
| **Task duration** | ≤15 min | N/A (LLM only) | ≤1 hr | Unlimited | Unlimited | Unlimited |
| **GPU support** | No | N/A | No | Yes | Yes | Yes |
| **Full conda env** | No | N/A | Yes (large image) | Yes | Yes | Yes |
| **Ops complexity** | Low | Lowest | Low | Medium | High | Low |
| **Auto-scaling** | Yes | Yes | Yes | Yes | Yes | No |
| **Cost at idle** | $0 | $0 | $0 | Low | Low | Full |
| **Data lake** | EFS | N/A | EFS | EFS/local | EFS/PVC | Local/EFS |
| **Best for** | Webhooks | Any tier | API service | GPU/batch | Production | Research |

## Recommended Stack

```
Researchers:       Raw EC2 (g5.2xlarge)  +  Bedrock LLM
API service:       ECS Fargate            +  Bedrock LLM
Production scale:  EKS                    +  Bedrock LLM + Spot nodes
Cost-sensitive:    ECS EC2 Spot           +  Bedrock LLM
```

---

## Shared Infrastructure (all options)

### Secrets Manager

```bash
# Store all API keys once; reference in all tiers
aws secretsmanager create-secret \
  --name biomni/anthropic-key \
  --secret-string "sk-ant-XXXXXXXX"

aws secretsmanager create-secret \
  --name biomni/openai-key \
  --secret-string "sk-XXXXXXXX"
```

### ECR Repository

```bash
aws ecr create-repository --repository-name biomni
# Add lifecycle policy to keep only last 10 images
aws ecr put-lifecycle-policy \
  --repository-name biomni \
  --lifecycle-policy-text '{
    "rules": [{
      "rulePriority": 1,
      "selection": {"tagStatus":"any","countType":"imageCountMoreThan","countNumber":10},
      "action": {"type":"expire"}
    }]
  }'
```

### EFS File System (shared data lake)

```bash
aws efs create-file-system \
  --performance-mode generalPurpose \
  --throughput-mode elastic \
  --encrypted \
  --tags Key=Name,Value=biomni-data

# Pre-populate data lake from S3 (faster than downloading inside container)
aws s3 sync s3://your-bucket/biomni-data /mnt/efs/biomni-data
```

### CI/CD Pipeline (CodePipeline)

```bash
# Trigger image rebuild on git push → run tests → push to ECR → update ECS/EKS
aws codepipeline create-pipeline --cli-input-json file://pipeline.json
```

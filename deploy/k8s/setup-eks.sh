#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Biomni EKS setup script
# Mirrors the current ECS Fargate deployment on Kubernetes.
#
# Prerequisites on your Mac:
#   brew install kubectl eksctl helm awscli
#
# Run: bash deploy/k8s/setup-eks.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CLUSTER="biomni-cluster"
REGION="us-west-2"
ACCOUNT="314567760197"
ECR_IMAGE="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/biomni:latest"
POLICY_ARN="arn:aws:iam::$ACCOUNT:policy/BiomniECSPolicy"

echo "=== Step 1: Create EKS cluster ==="
# ~15 minutes — creates 2 managed nodes (t3.medium) across 2 AZs
eksctl create cluster \
  --name "$CLUSTER" \
  --region "$REGION" \
  --nodegroup-name biomni-nodes \
  --node-type t3.medium \
  --nodes 2 \
  --nodes-min 2 \
  --nodes-max 6 \
  --managed \
  --with-oidc                        # needed for IRSA

echo "=== Step 2: Configure kubectl ==="
aws eks update-kubeconfig --region "$REGION" --name "$CLUSTER"

echo "=== Step 3: Create IAM service account (IRSA) ==="
# Grants the pod access to Bedrock + DynamoDB without hardcoded keys
eksctl create iamserviceaccount \
  --cluster "$CLUSTER" \
  --namespace biomni \
  --name biomni-sa \
  --attach-policy-arn "$POLICY_ARN" \
  --approve \
  --override-existing-serviceaccounts

# Capture the role ARN and patch serviceaccount.yaml
ROLE_ARN=$(aws iam list-roles \
  --query "Roles[?contains(RoleName, 'biomni-sa')].Arn" \
  --output text | head -1)
echo "IRSA role ARN: $ROLE_ARN"
sed -i.bak "s|arn:aws:iam::314567760197:role/biomni-eks-role|$ROLE_ARN|g" \
  deploy/k8s/serviceaccount.yaml

echo "=== Step 4: Install AWS Load Balancer Controller ==="
# Needed to create an ALB from our Ingress resource
helm repo add eks https://aws.github.io/eks-charts
helm repo update

# Create IRSA for the load balancer controller itself
curl -O https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.7.2/docs/install/iam_policy.json
aws iam create-policy \
  --policy-name AWSLoadBalancerControllerIAMPolicy \
  --policy-document file://iam_policy.json 2>/dev/null || true

eksctl create iamserviceaccount \
  --cluster "$CLUSTER" \
  --namespace kube-system \
  --name aws-load-balancer-controller \
  --attach-policy-arn "arn:aws:iam::$ACCOUNT:policy/AWSLoadBalancerControllerIAMPolicy" \
  --approve \
  --override-existing-serviceaccounts

helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName="$CLUSTER" \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller

echo "=== Step 5: Apply Biomni manifests ==="
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/serviceaccount.yaml
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/deployment.yaml
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/ingress.yaml
kubectl apply -f deploy/k8s/hpa.yaml

echo "=== Step 6: Wait for pods to be ready ==="
kubectl rollout status deployment/biomni-api -n biomni --timeout=300s

echo "=== Step 7: Get your URL ==="
echo "Waiting for ALB to be provisioned (~90 seconds)..."
sleep 90
kubectl get ingress biomni-ingress -n biomni \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
echo ""
echo "Open that URL in your browser."

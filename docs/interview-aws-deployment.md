# AWS Deployment — Interview Cheat Sheet
## Merck PKPD Team Context

---

## The One-Line Summary for Each Tier

| Tier | One sentence |
|---|---|
| **Lambda** | Serverless function — runs on demand, dies after 15 min, costs nothing at idle |
| **Bedrock** | AWS-managed LLM API — swap your Anthropic key for an IAM role, traffic stays inside AWS |
| **ECS Fargate** | Managed containers — you define CPU/RAM, AWS handles the server, scales to zero |
| **ECS EC2** | Your containers, your EC2 fleet — needed for GPU or >30 GB RAM |
| **EKS** | Kubernetes on AWS — orchestrates everything at production scale |
| **Raw EC2** | A plain virtual machine — install anything, full control, no abstractions |

---

## The Mental Model: Match Task Shape to Tier

```
Task is short + infrequent          →  Lambda
Task needs a big LLM, stay in AWS  →  Bedrock (add-on to any tier)
Task is a long-running API          →  ECS Fargate
Task needs GPU or lots of RAM       →  ECS EC2
Many teams sharing one platform     →  EKS
One scientist, full environment     →  Raw EC2
```

---

## Section 1 — Lambda

### What it is
An event-triggered function. You upload code (or a container image), AWS runs it
only when called, then shuts it down. You pay per invocation, not per hour.

### Hard limits that matter
- **15-minute max runtime** — any job longer than that will be killed
- **10 GB container image** — large conda environments won't fit
- **No GPU** — CPU only
- **Cold start** — first call after idle can take 30–60 seconds

### PKPD use case at Merck
> *"A scientist submits a one-off query: 'What is the predicted Cmax for
> compound X at 10 mg?' Lambda receives the API call, invokes Biomni
> with Bedrock as the LLM, returns the answer in under a minute,
> then shuts down. Nobody is billed for the 23 hours a day nobody
> is asking questions."*

- Drug interaction lookup (single query, fast)
- Triggering a downstream pipeline when a new NONMEM run lands in S3
- Webhook receiver: when a clinical trial upload completes → start analysis
- Literature screening for a single compound

### When NOT to use it for PKPD
- Population PK fitting (NONMEM runs can take hours)
- Whole-body PBPK simulations
- Full scRNA-seq or proteomics pipelines
- Anything needing R with heavy bioinformatics libraries

### Interview talking point
> *"Lambda is great for event-driven triggers and short analytical queries.
> For Merck's PKPD work, I'd use it as the front door — receive the request,
> validate it, then hand off to ECS or EKS for the heavy computation.
> The 15-minute limit is the wall you always hit with real pharmacometric models."*

---

## Section 2 — Bedrock

### What it is
Not a compute tier — a managed LLM inference API inside AWS. You call it exactly
like the Anthropic API, but authentication is an IAM role (no API key), and traffic
never leaves the AWS network.

### Why it matters
| Without Bedrock | With Bedrock |
|---|---|
| Anthropic API key in your secrets manager | IAM role attached to EC2/ECS/Lambda — no key |
| Traffic goes to api.anthropic.com (internet) | Traffic stays in AWS VPC |
| Rotate keys when someone leaves | Revoke IAM role — instant, auditable |
| Separate billing account with Anthropic | One AWS bill |

### PKPD use case at Merck
> *"Every tier — Lambda, Fargate, EKS, EC2 — uses Bedrock as the LLM backend.
> A pharmacometrician asks Biomni: 'Fit a two-compartment PK model to this
> dataset and suggest a dosing regimen.' Biomni reasons through it using
> Claude on Bedrock, generates R/NONMEM code, executes it, and returns results.
> Merck's security team is happy because the data never leaves AWS."*

- All natural language queries to Biomni go through Bedrock
- Regulatory document summarisation (internal VPC, no data leakage)
- Literature mining across PubMed / internal trial reports
- Generating NONMEM control stream templates from plain-English specs

### One-liner for interview
> *"Bedrock is the LLM layer that sits behind every other tier. It means
> I never have to rotate API keys and all model calls stay inside the
> corporate network — which matters a lot in a regulated pharma environment."*

---

## Section 3 — ECS Fargate

### What it is
You write a Docker container. AWS runs it without you managing any EC2 instance.
You specify CPU and RAM per task; AWS finds the hardware.

### Key properties
- **No EC2 to patch** — AWS handles OS updates, hardware failures
- **Scales to zero** — when no tasks are running, you pay nothing for compute
- **Max 4 vCPU / 30 GB RAM per task** — hard ceiling
- **No GPU** — CPU only
- **EFS mount** — persistent filesystem shared across all tasks (needed for the 11 GB Biomni data lake)

### PKPD use case at Merck
> *"The PKPD team runs population PK analyses throughout the day. Each
> analysis is submitted as an API call to a Fargate service. The service
> auto-scales from 1 task at 9 AM to 8 tasks by noon when the whole team
> is working, then scales back to 1 overnight. Nobody manages servers.
> The EFS filesystem holds the Biomni data lake — drug databases, gene
> ontologies, clinical trial datasets — shared across all tasks."*

- Population PK/PD model fitting (< 1 hour runs)
- Dose-response curve fitting and reporting API
- Biomarker analysis pipeline (multi-omics, if fits in 30 GB)
- Serving a REST API that clinical pharmacologists query from R/Python scripts

### ECS Fargate vs Lambda — key difference
```
Lambda:   stateless, ≤15 min, event-triggered, per-invocation billing
Fargate:  long-running service, persistent API, per-task-hour billing
```

### Interview talking point
> *"For Merck's PKPD platform I'd run the main Biomni API on Fargate.
> It's always up for the team to query, it scales automatically with
> workload, and we pay only for what we use. The EFS mount means every
> task sees the same drug databases without duplication."*

---

## Section 4 — ECS on EC2

### What it is
ECS (same container orchestration as Fargate), but instead of AWS managing the
underlying hardware, you run your own Auto Scaling Group of EC2 instances.
The ECS scheduler places containers onto your instances.

### Why you'd choose this over Fargate
- **GPU access** — Fargate has no GPU; g5/p3 instances do
- **> 30 GB RAM** — Fargate caps at 30 GB; r6i.8xlarge has 256 GB
- **Spot instances** — 60–80% cost reduction for interruptible batch workloads
- **Custom hardware** — high-memory, high-storage, custom AMIs

### PKPD use case at Merck
> *"Running Biomni with a local Qwen-32B reasoning model instead of
> Bedrock — useful when the PKPD team wants to run completely air-gapped
> for a sensitive trial. The SGLang container runs on a g5.2xlarge
> GPU instance; the Biomni container connects to it on localhost.
> Spot instances keep the cost manageable for overnight batch runs."*

- PBPK (physiologically-based PK) modelling — can run hours, needs 64–128 GB RAM
- Deep learning ADMET prediction (absorption, distribution, metabolism,
  excretion, toxicity) — needs GPU
- Large-scale virtual screening across compound libraries
- Overnight batch: re-fit 500 population PK models for a regulatory submission

### ECS EC2 vs ECS Fargate — key difference
```
Fargate:  AWS manages servers. Max 30 GB RAM. No GPU. Easy.
ECS EC2:  You manage servers. Unlimited RAM. GPU yes. More work.
```

### Interview talking point
> *"I'd use ECS EC2 for the compute-intensive PKPD workloads — PBPK
> simulations, ADMET deep learning, or any job that needs more than
> 30 GB of RAM. Spot instances make it cost-effective for batch work
> that can tolerate interruption. The operational overhead is worth
> it for GPU access."*

---

## Section 5 — EKS (Kubernetes)

### What it is
AWS's managed Kubernetes service. Kubernetes is an open-source system for
running and scaling containers across a cluster of machines. EKS handles
the Kubernetes control plane (the part that decides where containers run);
you manage the worker node groups.

### Why Kubernetes exists
Without Kubernetes, if you have 10 different services (Biomni API, SGLang,
a NONMEM job runner, a reporting service, a data ingestion pipeline...),
you need 10 separate ECS services or EC2 setups, each with their own
scaling rules, networking, secrets management, and deployment process.
Kubernetes manages all of them with a unified interface.

### Key concepts (simple versions)
| Kubernetes term | Plain English |
|---|---|
| Pod | One running container (or a small group of tightly-coupled containers) |
| Deployment | "Keep 3 copies of this container running at all times" |
| Service | A stable address to reach your pods (load-balances across them) |
| Ingress | The front door — routes external HTTP traffic to the right service |
| HPA | Auto-scaler — adds/removes pods based on CPU, memory, or custom metrics |
| Namespace | A folder for grouping related resources (e.g. `biomni`, `nonmem`, `reporting`) |
| Node group | A pool of EC2 instances (CPU pool, GPU pool, etc.) |

### PKPD use case at Merck
> *"Merck's computational pharmacology platform serves five teams:
> clinical pharmacology, biostatistics, translational science,
> regulatory affairs, and discovery. Each team runs different workloads
> with different compute needs. EKS runs them all on one cluster:
> CPU spot nodes for routine PK/PD fits, GPU nodes (scale to zero
> overnight) for ADMET deep learning, and system nodes for shared
> infrastructure. One cluster, one bill, one deployment process."*

- Multi-team platform: each team in their own Kubernetes namespace
- GitOps: pharmacometricians submit new models via pull request;
  CI/CD deploys automatically on merge
- Blue/green deployments: roll out a new Biomni version to 10% of traffic
  first, check results, then promote — critical for a validated system
- Cluster Autoscaler spins up GPU nodes when a deep learning job arrives,
  terminates them when done — zero cost at idle

### EKS vs ECS — key difference
```
ECS:  AWS-specific. Simpler. Two flavours (Fargate/EC2). Good for 1–5 services.
EKS:  Kubernetes standard. Steeper learning curve. Better for 5+ services,
      multi-team, GitOps, or if you already run Kubernetes on-prem.
```

### Interview talking point
> *"For Merck's PKPD platform at scale — multiple teams, multiple
> workload types, a need for validated deployments — I'd choose EKS.
> The Kubernetes abstraction lets each team deploy their own services
> independently while sharing the underlying compute. HPA and Cluster
> Autoscaler mean the GPU nodes only exist when needed. The tradeoff
> is operational complexity: you need someone who knows Kubernetes."*

---

## Section 6 — Raw EC2

### What it is
A virtual machine. You pick the OS, the instance type, and you install
whatever you want. No containerisation, no orchestration layer.

### Why you'd use it
- Full conda environment (30 GB+, R, all bioinformatics CLI tools)
- Simplest setup — SSH in, run commands, done
- One researcher doing exploratory analysis
- Tools that are hard to containerise (GUI apps, complex system dependencies)

### PKPD use case at Merck
> *"A pharmacometrician is doing exploratory PBPK modelling with PK-Sim
> and MoBi, plus custom R scripts pulling from internal databases.
> Raw EC2 (r6i.8xlarge, 256 GB RAM) with the full Biomni conda
> environment. They SSH in in the morning, run analyses all day through
> the Gradio UI, and an auto-stop rule shuts the instance down at midnight.
> No containers, no YAML — just a scientist and a powerful machine."*

- Interactive modelling sessions (Gradio UI in the browser)
- Installing proprietary tools (NONMEM licence, Simcyp, PK-Sim)
- Full PBPK environment with all dependencies
- One-off regulatory submission analysis

### Raw EC2 vs everything else
```
Everything else: designed for repeatable, automated, scalable deployments
Raw EC2:         designed for a person doing science interactively
```

### Interview talking point
> *"I'd give every pharmacometrician on the PKPD team a personal EC2
> instance — r6i.2xlarge for routine work, g5.2xlarge if they need
> local models. The Biomni Gradio UI runs in the browser. An auto-stop
> cron job prevents runaway costs. It's the lowest-friction way to get
> a scientist productive on day one."*

---

## Section 7 — CI/CD

### What is CI/CD?

**CI = Continuous Integration** — every time code is pushed, automated tests run.  
**CD = Continuous Deployment** — if tests pass, the new version is deployed automatically.

Without it:
```
You change code → manually build Docker image → manually push to ECR
→ manually update ECS service (or Lambda or kubectl) → hope you didn't forget a step
```

With it:
```
You push to GitHub → everything else happens automatically
```

### When you need CI/CD vs when you don't

| Situation | Need CI/CD? |
|---|---|
| Solo researcher, deploying once a week | No — manual is fine |
| Team of 3+, deploying daily | Yes — manual becomes error-prone |
| Multiple deployment targets (Lambda + Fargate + EKS) | Yes — too many commands to run by hand |
| Need tests to pass before deploying | Yes — can't enforce that manually at scale |
| Regulated environment needing deployment audit trail | Yes — CI/CD logs every deploy with commit SHA |

### The pipeline for Biomni (`.github/workflows/ci-cd.yml`)

```
git push to main
       │
       ▼
  [1] Test         run pytest — catch broken imports, bad configs
       │
       ▼
  [2] Build        docker build (layer-cached — fast after first time)
                   docker push to ECR with commit SHA as tag
       │
    ┌──┴───────────────────────────┐
    ▼                              ▼
  [3a] Deploy Lambda        [3b] Deploy Fargate
  update function image     register new task def
  publish version alias     update ECS service
    │                              │
    ▼                              ▼
  [3c] Deploy ECS EC2       [3d] Deploy EKS
  update GPU task def       kubectl set image
  update service            wait for rollout
```

Jobs 3a–3d run in parallel, controlled by the `DEPLOY_TARGET` secret.

### PKPD use case at Merck
> *"A computational scientist fixes a bug in Biomni's NONMEM output parser.
> She pushes to main. GitHub Actions builds a new image (2 minutes, layer-cached),
> runs the test suite, then updates the ECS Fargate service — the PKPD team's
> production API — without anyone touching a terminal. The deployment is logged
> with the commit SHA, the author, and the timestamp. The regulatory affairs
> team can audit exactly what version was running during any trial analysis."*

### Key concept: image tagging with commit SHA

```bash
# Bad:  docker push biomni:latest
# Good: docker push biomni:a3f7c2e  (git commit SHA)
```

Using `latest` means you can never tell which code is actually running.
Using the commit SHA means every deployment is traceable to exact source code —
critical in a validated pharma environment (GxP compliance).

### Interview talking point
> *"CI/CD is the difference between deployment being a careful manual ceremony
> and it being a non-event. For Merck's PKPD platform, the audit trail matters
> as much as the automation — regulators may ask 'what version of the model
> was running when this trial analysis was generated?' With CI/CD and commit SHA
> tagging, that question has a precise answer in 30 seconds."*

---

## Quick Comparison Table

| | Lambda | Bedrock | ECS Fargate | ECS EC2 | EKS | Raw EC2 |
|---|---|---|---|---|---|---|
| Who manages servers | AWS | AWS | AWS | You | You (nodes) | You |
| Docker needed | Yes | No | Yes | Yes | Yes | No |
| Kubernetes needed | No | No | No | No | Yes | No |
| Max runtime | 15 min | ∞ | ∞ | ∞ | ∞ | ∞ |
| GPU | No | N/A | No | Yes | Yes | Yes |
| Scales to zero | Yes | Yes | Yes | Partial | Yes | No |
| Best for at Merck | Event triggers | All tiers | PKPD API | PBPK/ADMET | Multi-team platform | Single scientist |
| Complexity | Low | None | Low | Medium | High | Low |

---

## Likely Interview Questions + Answers

**Q: Why not just use Lambda for everything?**
> Lambda has a 15-minute hard limit and no GPU. Population PK fitting
> with NONMEM, PBPK simulations, or any deep learning ADMET model will
> blow past 15 minutes. Lambda is the right tool for short event-driven
> tasks; ECS or EKS handles the heavy lifting.

**Q: What's the difference between ECS Fargate and ECS EC2?**
> Fargate is serverless containers — AWS manages the underlying EC2.
> ECS EC2 is containers on EC2 you manage yourself. The trade-off is
> simplicity vs control. For PKPD, I'd use Fargate for routine analyses
> (fits in 30 GB RAM, no GPU), and ECS EC2 when I need GPU for ADMET
> deep learning or more than 30 GB RAM for PBPK.

**Q: Why Kubernetes over ECS?**
> ECS is simpler and AWS-native — the right choice for 1–5 services.
> Kubernetes (EKS) pays off when you have multiple teams with different
> workloads sharing infrastructure, need GitOps workflows, or want
> portability (same manifests work on-prem or on another cloud).
> For a multi-team Merck PKPD platform, I'd choose EKS. For a
> small team's internal tool, ECS Fargate.

**Q: Why use Bedrock instead of calling Anthropic's API directly?**
> Three reasons: no API keys to rotate (IAM role instead), all traffic
> stays inside AWS (important for sensitive trial data), and one
> consolidated AWS bill. In a GxP environment, not having credentials
> to manage is a meaningful compliance simplification.

**Q: When would you use Raw EC2?**
> When a scientist needs an interactive environment with proprietary
> licensed tools (NONMEM, Simcyp, PK-Sim), or when the full 30 GB+
> conda environment with R and all bioinformatics CLI tools is needed.
> It's the fastest path to productivity for a single researcher.
> It doesn't scale, but it doesn't need to.

**Q: Why does CI/CD matter in a regulated pharma environment?**
> Regulators can ask what software version produced a given analysis.
> CI/CD with commit SHA tagging means every deployment is tied to
> exact source code, the person who authored it, and the time it was
> deployed — all logged in GitHub Actions. That audit trail is
> difficult to reconstruct from manual deployments.

**Q: Walk me through a deployment from code change to production.**
> Scientist pushes a bug fix to main. GitHub Actions runs pytest.
> If tests pass, Docker builds a new image (layer-cached — takes
> 2–3 minutes, not 40). The image is pushed to ECR tagged with the
> commit SHA. In parallel, the pipeline updates the ECS Fargate
> service (registers a new task definition, triggers a rolling update)
> and updates the EKS deployment (kubectl set image, waits for
> rollout). The old tasks drain gracefully; new tasks start with
> the fixed code. Total time: ~5 minutes, zero manual steps.

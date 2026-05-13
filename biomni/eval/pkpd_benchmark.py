"""PKPD agent benchmark using LangSmith evaluate().

Three evaluation layers:
  Layer 1 — Numerical accuracy   NCA parameters, CLint, DDI R1 vs ground truth
  Layer 2 — Decision quality     Pharmacometric classification / reasoning
  Layer 3 — Workflow completeness End-to-end output contains required elements

Quick start
-----------
# 1. Set env vars in .env (copy from .env.example)
#    LANGCHAIN_TRACING_V2=true
#    LANGCHAIN_API_KEY=ls__...
#    LANGCHAIN_PROJECT=biomni-pkpd

# 2. Upload dataset once
python -m biomni.eval.pkpd_benchmark --upload-dataset

# 3. Run benchmark (results appear in LangSmith UI)
python -m biomni.eval.pkpd_benchmark --run

# 4. Compare models
python -m biomni.eval.pkpd_benchmark --run --model claude-opus-4-7-20250514 --experiment pkpd-opus
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Dataset
# ─────────────────────────────────────────────────────────────────────────────
# Layer 1a: NCA
# Ground truth from 1-cmt IV model: C(t) = (Dose/Vd)*exp(-CL/Vd * t)
# Case 1: CL=5 L/h, Vd=50 L  → t½=6.93 h, AUCinf=Dose/CL=20 h·mg/L
# Case 2: oral 1-cmt, CL/F=6 L/h, Vd/F=30 L, KA=1.2 /h
NCA_CASES = [
    {
        "id": "nca_1cmt_iv_100mg",
        "description": "1-cmt IV bolus 100 mg — CL=5 L/h, Vd=50 L",
        "inputs": {
            "prompt": (
                "Run NCA on this IV bolus PK data. Dose=100 mg, route=iv.\n"
                "Time (h): [0.5, 1, 2, 4, 6, 8, 12, 24]\n"
                "Conc (mg/L): [1.900, 1.811, 1.637, 1.340, 1.097, 0.898, 0.601, 0.202]\n\n"
                "Return ONLY a JSON object with keys: "
                "AUCinf_h_mg_L, t_half_h, CL_L_h, Vd_L. No other text."
            ),
        },
        "outputs": {
            "AUCinf_h_mg_L": 20.0,
            "t_half_h": 6.93,
            "CL_L_h": 5.0,
            "Vd_L": 50.0,
            "tolerance": 0.10,
        },
    },
    {
        "id": "nca_1cmt_oral_50mg",
        "description": "1-cmt oral 50 mg — CL/F=6 L/h, Vd/F=30 L, KA=1.2 /h",
        "inputs": {
            "prompt": (
                "Run NCA on this oral dose PK data. Dose=50 mg, route=oral.\n"
                "Time (h): [0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 24]\n"
                "Conc (ng/mL): [580, 910, 1100, 1190, 1230, 1170, 950, 720, 390, 70]\n\n"
                "Return ONLY a JSON object with keys: "
                "Cmax_ng_mL, Tmax_h, AUCinf_h_ng_mL, t_half_h, CL_F_L_h. No other text."
            ),
        },
        "outputs": {
            "Cmax_ng_mL": 1230.0,
            "Tmax_h": 3.0,
            "AUCinf_h_ng_mL": 10900.0,
            "t_half_h": 3.47,
            "CL_F_L_h": 4.59,
            "tolerance": 0.15,
        },
    },
]

# Layer 1b: CLint
# CLint (µL/min/mg) = -slope(ln fraction) × V_incubation_mL × 1000 / protein_mg
# Case 1: slope=-0.0578/min → CLint≈115 µL/min/mg → HIGH (t½<30 min)
# Case 2: slope=-0.00770/min → CLint≈15 µL/min/mg → LOW (t½>60 min)
CLINT_CASES = [
    {
        "id": "clint_high",
        "description": "High CLint: t½≈12 min → CLint≈115 µL/min/mg",
        "inputs": {
            "prompt": (
                "Calculate intrinsic clearance from this microsomal stability data.\n"
                "Time (min): [0, 5, 10, 20, 40, 60]\n"
                "% Remaining: [100, 74.1, 54.9, 30.1, 9.1, 2.7]\n"
                "Microsomal protein: 0.5 mg/mL, incubation volume: 0.5 mL\n\n"
                "Return ONLY a JSON object with keys: "
                "CLint_uL_min_mg, t_half_min, clearance_class. "
                "clearance_class must be HIGH, MEDIUM, or LOW. No other text."
            ),
        },
        "outputs": {
            "CLint_uL_min_mg": 115.5,
            "t_half_min": 12.0,
            "clearance_class": "HIGH",
            "tolerance": 0.15,
        },
    },
    {
        "id": "clint_low",
        "description": "Low CLint: t½≈90 min → CLint≈15 µL/min/mg",
        "inputs": {
            "prompt": (
                "Calculate intrinsic clearance from this microsomal stability data.\n"
                "Time (min): [0, 15, 30, 60, 90, 120]\n"
                "% Remaining: [100, 89.5, 80.1, 64.2, 51.4, 41.2]\n"
                "Microsomal protein: 0.5 mg/mL, incubation volume: 0.5 mL\n\n"
                "Return ONLY a JSON object with keys: "
                "CLint_uL_min_mg, t_half_min, clearance_class. "
                "clearance_class must be HIGH, MEDIUM, or LOW. No other text."
            ),
        },
        "outputs": {
            "CLint_uL_min_mg": 15.4,
            "t_half_min": 90.0,
            "clearance_class": "LOW",
            "tolerance": 0.15,
        },
    },
]

# Layer 1c: DDI R1
# R1 = 1 + ([I]max,total × fu) / IC50
# Case 1: 1 + (2.0×0.1)/0.5 = 1.40 → HIGH (≥1.02, clinical study required)
# Case 2: 1 + (0.5×0.05)/50 = 1.0005 → LOW (<1.02, no action)
DDI_CASES = [
    {
        "id": "ddi_r1_high",
        "description": "R1=1.40 — clinical DDI study required",
        "inputs": {
            "prompt": (
                "Calculate FDA 2020 R1 for CYP3A4 inhibition.\n"
                "IC50 = 0.5 µM, Cmax,total = 2.0 µM, fu,plasma = 0.10\n\n"
                "Return ONLY a JSON object with keys: "
                "R1, risk_tier, clinical_ddi_study_required. "
                "risk_tier must be LOW, MODERATE, or HIGH. "
                "clinical_ddi_study_required must be true or false. No other text."
            ),
        },
        "outputs": {
            "R1": 1.40,
            "risk_tier": "HIGH",
            "clinical_ddi_study_required": True,
            "tolerance": 0.05,
        },
    },
    {
        "id": "ddi_r1_low",
        "description": "R1=1.0005 — no further action",
        "inputs": {
            "prompt": (
                "Calculate FDA 2020 R1 for CYP2D6 inhibition.\n"
                "IC50 = 50 µM, Cmax,total = 0.5 µM, fu,plasma = 0.05\n\n"
                "Return ONLY a JSON object with keys: "
                "R1, risk_tier, clinical_ddi_study_required. "
                "risk_tier must be LOW, MODERATE, or HIGH. "
                "clinical_ddi_study_required must be true or false. No other text."
            ),
        },
        "outputs": {
            "R1": 1.0005,
            "risk_tier": "LOW",
            "clinical_ddi_study_required": False,
            "tolerance": 0.05,
        },
    },
]

# Layer 2: Decision / reasoning
DECISION_CASES = [
    {
        "id": "decision_cwres_u_shape",
        "description": "U-shaped CWRES vs TIME → missing peripheral compartment",
        "inputs": {
            "prompt": (
                "The CWRES vs TIME plot shows a U-shaped trend: negative residuals early, "
                "positive in mid-phase, negative again late. "
                "What is the most likely cause and what structural model change is needed?"
            ),
        },
        "outputs": {
            "required": ["distribution", "peripheral", "compartment"],
            "forbidden": ["lag time", "absorption", "residual error"],
        },
    },
    {
        "id": "decision_eta_shrinkage_high",
        "description": "ETA shrinkage 45% → EBEs unreliable for covariate screening",
        "inputs": {
            "prompt": (
                "ETA shrinkage for CL is 45%. "
                "Can I use individual EBEs for covariate screening? What does this indicate?"
            ),
        },
        "outputs": {
            "required": ["no", "30%", "unreliable", "sparse"],
            "forbidden": ["yes", "acceptable", "reliable"],
        },
    },
    {
        "id": "decision_vpc_pi_too_wide",
        "description": "VPC PI too wide → over-estimated IIV",
        "inputs": {
            "prompt": (
                "In my VPC the 90% prediction interval is much wider than the observed data — "
                "most observations fall well inside the PI. What does this mean and how to fix it?"
            ),
        },
        "outputs": {
            "required": ["over-estimated", "IIV", "variability", "covariate"],
            "forbidden": ["under-estimated", "add compartment"],
        },
    },
    {
        "id": "decision_omega_rse_high",
        "description": "OMEGA RSE 85% → too imprecise, model needs simplification",
        "inputs": {
            "prompt": (
                "The RSE for an OMEGA parameter in my popPK model is 85%. "
                "Is this acceptable and what should I do?"
            ),
        },
        "outputs": {
            "required": ["50%", "not acceptable", "simplif"],
            "forbidden": ["acceptable", "proceed"],
        },
    },
    {
        "id": "decision_tdi_detected",
        "description": "IC50 shift ≥1.5 → TDI / mechanism-based inhibition",
        "inputs": {
            "prompt": (
                "CYP3A4 IC50 without preincubation = 5 µM. "
                "After 30-min NADPH preincubation it drops to 1.8 µM. "
                "What does this indicate and what is the next experimental step?"
            ),
        },
        "outputs": {
            "required": ["time-dependent", "mechanism-based", "kinact", "KI"],
            "forbidden": ["reversible", "no further action"],
        },
    },
    {
        "id": "decision_fan_shaped_cwres",
        "description": "Fan-shaped CWRES vs PRED → proportional error not captured",
        "inputs": {
            "prompt": (
                "CWRES vs PRED shows a fan-shaped pattern: residual variance increases "
                "as predicted concentration increases. What is the issue and how to fix it?"
            ),
        },
        "outputs": {
            "required": ["proportional", "error model", "variance"],
            "forbidden": ["additive only", "compartment"],
        },
    },
]

# Layer 3: Workflow completeness
WORKFLOW_CASES = [
    {
        "id": "workflow_nca_completeness",
        "description": "NCA report must contain all required elements",
        "inputs": {
            "prompt": (
                "Run a complete NCA workflow on this IV bolus data. Dose=100 mg.\n"
                "Time (h): [0.5, 1, 2, 4, 6, 8, 12, 24]\n"
                "Conc (mg/L): [1.900, 1.811, 1.637, 1.340, 1.097, 0.898, 0.601, 0.202]\n"
                "Provide a full NCA report."
            ),
        },
        "outputs": {
            "required_terms": [
                "AUClast", "AUCinf", "Cmax", "t½",
                "CL", "lambda", "R²", "extrapolation",
            ],
        },
    },
    {
        "id": "workflow_dmpk_completeness",
        "description": "DMPK report must flag all key ADME concerns",
        "inputs": {
            "prompt": (
                "Summarise the ADME profile for Compound-X:\n"
                "- Microsomal t½ = 10 min (high clearance)\n"
                "- fu,plasma = 0.005 (very high protein binding)\n"
                "- Caco-2 Papp A→B = 0.3 × 10⁻⁶ cm/s (low permeability)\n"
                "- CYP3A4 IC50 = 0.3 µM\n"
                "- Efflux ratio = 3.2\n"
                "Provide a complete ADME assessment with red flags and next steps."
            ),
        },
        "outputs": {
            "required_terms": [
                "high clearance", "protein binding", "permeability",
                "P-gp", "DDI", "oral bioavailability", "next", "recommend",
            ],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Evaluators
# ─────────────────────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{[^{}]+\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


def numerical_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    """Relative-error scoring for computed numeric parameters."""
    skip = {"tolerance", "required", "forbidden", "required_terms"}
    numeric_keys = [
        k for k, v in reference_outputs.items()
        if k not in skip and isinstance(v, (int, float))
    ]
    if not numeric_keys:
        return {"key": "numerical_accuracy", "score": None}

    parsed = _parse_json(outputs.get("output", ""))
    if parsed is None:
        return {"key": "numerical_accuracy", "score": 0.0,
                "comment": "no JSON found in output"}

    tol = reference_outputs.get("tolerance", 0.10)
    passes, comments = 0, []
    for key in numeric_keys:
        truth = reference_outputs[key]
        pred = parsed.get(key)
        if pred is None:
            comments.append(f"{key}: MISSING")
            continue
        try:
            err = abs(float(pred) - float(truth)) / (abs(float(truth)) + 1e-10)
            ok = err <= tol
            passes += ok
            comments.append(f"{key}: pred={pred:.4g} truth={truth:.4g} err={err:.1%} {'✓' if ok else '✗'}")
        except (TypeError, ValueError):
            comments.append(f"{key}: unparseable (pred={pred})")

    score = passes / len(numeric_keys)
    return {"key": "numerical_accuracy", "score": round(score, 3),
            "comment": " | ".join(comments)}


def classification_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    """Exact-match scoring for categorical / boolean parameters."""
    skip = {"tolerance", "required", "forbidden", "required_terms"}
    cat_keys = [
        k for k, v in reference_outputs.items()
        if k not in skip and isinstance(v, (str, bool))
    ]
    if not cat_keys:
        return {"key": "classification_accuracy", "score": None}

    parsed = _parse_json(outputs.get("output", ""))
    if parsed is None:
        return {"key": "classification_accuracy", "score": 0.0,
                "comment": "no JSON found in output"}

    passes, comments = 0, []
    for key in cat_keys:
        truth = reference_outputs[key]
        pred = parsed.get(key)
        if pred is None:
            comments.append(f"{key}: MISSING")
            continue
        if isinstance(truth, bool):
            ok = str(pred).lower().strip() in ("true", "1", "yes") if truth \
                else str(pred).lower().strip() in ("false", "0", "no")
        else:
            ok = str(pred).strip().upper() == str(truth).strip().upper()
        passes += ok
        comments.append(f"{key}: pred={pred} truth={truth} {'✓' if ok else '✗'}")

    score = passes / len(cat_keys)
    return {"key": "classification_accuracy", "score": round(score, 3),
            "comment": " | ".join(comments)}


def reasoning_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    """Keyword precision/recall for pharmacometric reasoning outputs."""
    required = reference_outputs.get("required")
    if required is None:
        return {"key": "reasoning_quality", "score": None}

    text = outputs.get("output", "").lower()
    forbidden = reference_outputs.get("forbidden", [])

    hits = [kw for kw in required if kw.lower() in text]
    false_pos = [kw for kw in forbidden if kw.lower() in text]

    precision = len(hits) / len(required) if required else 1.0
    score = max(0.0, precision - 0.25 * len(false_pos))
    return {
        "key": "reasoning_quality",
        "score": round(score, 3),
        "comment": f"hits={hits} | penalised_for={false_pos}",
    }


def completeness_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    """Fraction of required reporting terms present in workflow output."""
    required = reference_outputs.get("required_terms")
    if required is None:
        return {"key": "workflow_completeness", "score": None}

    text = outputs.get("output", "").lower()
    present = [t for t in required if t.lower() in text]
    missing = [t for t in required if t.lower() not in text]
    score = len(present) / len(required)
    return {
        "key": "workflow_completeness",
        "score": round(score, 3),
        "comment": f"present={present} | missing={missing}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dataset upload
# ─────────────────────────────────────────────────────────────────────────────

DATASET_NAME = "biomni-pkpd-benchmark-v1"

ALL_CASES = [*NCA_CASES, *CLINT_CASES, *DDI_CASES, *DECISION_CASES, *WORKFLOW_CASES]


def upload_dataset() -> str:
    """Create the PKPD benchmark dataset in LangSmith (run once)."""
    from langsmith import Client

    client = Client()

    if client.has_dataset(dataset_name=DATASET_NAME):
        client.delete_dataset(dataset_name=DATASET_NAME)
        print(f"Replaced existing dataset '{DATASET_NAME}'")

    dataset = client.create_dataset(
        DATASET_NAME,
        description=(
            "Biomni PKPDAgent benchmark: NCA numerical accuracy, CLint calculation, "
            "DDI R1 computation, pharmacometric reasoning, workflow completeness."
        ),
    )

    client.create_examples(
        inputs=[c["inputs"] for c in ALL_CASES],
        outputs=[c["outputs"] for c in ALL_CASES],
        metadata=[{"id": c["id"], "description": c["description"]} for c in ALL_CASES],
        dataset_id=dataset.id,
    )

    print(f"Uploaded {len(ALL_CASES)} examples to '{DATASET_NAME}'")
    print(f"  NCA cases:       {len(NCA_CASES)}")
    print(f"  CLint cases:     {len(CLINT_CASES)}")
    print(f"  DDI R1 cases:    {len(DDI_CASES)}")
    print(f"  Decision cases:  {len(DECISION_CASES)}")
    print(f"  Workflow cases:  {len(WORKFLOW_CASES)}")
    return dataset.id


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark runner
# ─────────────────────────────────────────────────────────────────────────────

def _make_target(agent: Any):
    """Wrap PKPDAgent.go() into the LangSmith target signature."""
    def target(inputs: dict) -> dict:
        try:
            return {"output": agent.go(inputs["prompt"])}
        except Exception as exc:
            return {"output": f"ERROR: {exc}"}
    return target


def run_benchmark(
    agent: Any,
    experiment_prefix: str = "pkpd-benchmark",
    model_label: str | None = None,
    max_concurrency: int = 1,
) -> Any:
    """Run the full PKPD benchmark and log every result to LangSmith.

    Parameters
    ----------
    agent:
        Initialised PKPDAgent (or A1) instance.
    experiment_prefix:
        Prefix for the LangSmith experiment name — shown in the UI.
    model_label:
        Stored as metadata (e.g. "claude-sonnet-4-20250514").
    max_concurrency:
        Keep at 1 to avoid LLM rate-limits; raise for parallel API tiers.

    Returns
    -------
    LangSmith ExperimentResults — call ``.to_pandas()`` for a DataFrame.
    """
    from langsmith import evaluate

    metadata = {"model": model_label or str(getattr(agent, "llm", "unknown"))}
    print(f"\nRunning PKPD benchmark")
    print(f"  Dataset:    {DATASET_NAME}")
    print(f"  Experiment: {experiment_prefix}")
    print(f"  Metadata:   {metadata}\n")

    results = evaluate(
        _make_target(agent),
        data=DATASET_NAME,
        evaluators=[
            numerical_evaluator,
            classification_evaluator,
            reasoning_evaluator,
            completeness_evaluator,
        ],
        experiment_prefix=experiment_prefix,
        metadata=metadata,
        max_concurrency=max_concurrency,
    )

    _print_summary(results)
    return results


def _print_summary(results: Any) -> None:
    try:
        df = results.to_pandas()
        metric_cols = [c for c in df.columns if c.startswith("feedback.")]
        if not metric_cols:
            return
        print("\n── Benchmark Summary ──────────────────────────────────────")
        for col in metric_cols:
            name = col.replace("feedback.", "")
            vals = df[col].dropna()
            if vals.empty:
                continue
            print(f"  {name:<30}  mean={vals.mean():.3f}  n={len(vals)}")
        print("────────────────────────────────────────────────────────────\n")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Biomni PKPD agent benchmark")
    parser.add_argument(
        "--upload-dataset", action="store_true",
        help="Upload benchmark dataset to LangSmith (run once per version bump)",
    )
    parser.add_argument(
        "--run", action="store_true",
        help="Run benchmark and log results to LangSmith",
    )
    parser.add_argument(
        "--model", default="claude-sonnet-4-20250514",
        help="LLM model ID (default: claude-sonnet-4-20250514)",
    )
    parser.add_argument(
        "--source", default="Anthropic",
        help="LLM source provider (default: Anthropic)",
    )
    parser.add_argument(
        "--experiment", default="pkpd-benchmark",
        help="LangSmith experiment prefix",
    )
    args = parser.parse_args()

    if args.upload_dataset:
        upload_dataset()

    if args.run:
        from biomni.agent.pkpd_agent import PKPDAgent, enable_langsmith

        enable_langsmith()  # reads LANGCHAIN_API_KEY + LANGCHAIN_PROJECT from env
        agent = PKPDAgent(
            llm=args.model,
            source=args.source,
            expected_data_lake_files=[],
        )
        run_benchmark(
            agent,
            experiment_prefix=args.experiment,
            model_label=args.model,
        )

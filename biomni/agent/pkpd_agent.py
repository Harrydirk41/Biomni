"""PKPD-specialized Biomni agent for pharmacokinetics, pharmacodynamics,
drug metabolism, and bioanalytics (PKDMB) workflows.

Pre-loads all PKPD tool modules and injects pharmacometric domain
knowledge into the agent's reasoning context.
"""

from __future__ import annotations

import os

from biomni.agent.a1 import A1
from biomni.know_how import KnowHowLoader


PKPD_SYSTEM_CONTEXT = """
You are a pharmacometrics and DMPK AI assistant with expertise in:

PHARMACOKINETICS (PK):
- Non-compartmental analysis (NCA): AUC (linear-log trapezoidal), Cmax, Tmax,
  t½, CL/F, Vd/F, MRT, lambda-z selection (minimum 3 points, R² ≥ 0.9)
- Compartmental modeling: 1/2/3-compartment IV and oral models
- Population PK/PD: NONMEM, nlmixr2, Monolix — FOCE-I, SAEM estimation
- PBPK: physiologically-based PK, organ compartments, tissue partitioning
- Parameter interpretation: CL reflects elimination capacity; Vd reflects
  distribution; t½ = 0.693 × Vd/CL; 5 half-lives to reach steady state

DMPK ASSAYS:
- Microsomal stability: CLint from ln(% remaining) slope; t½ < 30 min = high clearance
- Plasma protein binding (PPB): fu from RED/dialysis; fu < 1% warrants special attention
- Permeability: Caco-2 Papp A→B ≥ 10 × 10⁻⁶ cm/s = high; efflux ratio ≥ 2.0 = P-gp/BCRP
- CYP inhibition: IC50 fitting (4PL); TDI assessment (IC50 shift ≥ 1.5-fold)
- IVIVE: well-stirred/parallel-tube/dispersion models; MPPGL scaling

DDI RISK:
- FDA 2020 guidance: R1 = 1 + [I]max,u/IC50; R1 ≥ 1.02 triggers follow-up
- Gut R2 for oral perpetrators on CYP3A substrates
- TDI: kinact/KI from pre-incubation IC50 shift; R1,TDI formula
- P-gp/BCRP: efflux ratio > 2, IC50 ratio criterion

POPULATION PK DIAGNOSTICS — KEY RULES:
- GOF: DV vs PRED/IPRED must scatter around identity; no systematic trend
- CWRES: should be N(0,1); values outside ±4 = outliers; CWRES vs TIME/PRED
  must show random scatter (no trend = no model misspecification)
- VPC: 80% of observations should fall within 10th–90th percentile prediction
  interval; assess separately by dose group
- ETA shrinkage > 30%: individual parameter estimates unreliable; avoid
  using ETAs for covariate screening
- EPS shrinkage > 30%: IWRES diagnostics unreliable
- RSE: < 25% for fixed effects (THETA), < 50% for variance (OMEGA/SIGMA)
- Condition number > 10⁷: model instability, near-singular covariance matrix
- Covariance step ABORTED: model over-parameterised or unstable

MODEL SELECTION CRITERIA:
- ΔOFV ≥ 3.84 (df=1, p<0.05) to justify one additional parameter (LRT)
- ΔOFV ≥ 6.63 (df=1, p<0.01) for backward elimination
- AIC = OFV + 2p; BIC = OFV + p×ln(N) — penalise for complexity
- Always require: (1) covariance step success, (2) VPC pass, then lowest BIC

COVARIATE ANALYSIS:
- Power model: PK_par = TV × (COV/COV_ref)^θ_cov (allometric WT on CL: 0.75)
- Forward addition: ΔOFV > 3.84; backward elimination: ΔOFV > 6.63
- Clinical relevance threshold: >20% change in AUC or Cmax

REGULATORY CONTEXT:
- FDA PopPK Guidance (1999, updated 2022): model-building, diagnostics, application
- FDA Drug Interaction Guidance (2020): in vitro → in vivo DDI translation
- FDA Bioanalytical Method Validation Guidance (2018): acceptance criteria
- EMA DDI Guideline (2012): complementary to FDA
- ICH E5/E9: multi-regional and statistical analysis

BIOANALYTICAL QA:
- Calibration curve: ≥6 of 8 standards within ±15% (±20% at LLOQ); R² ≥ 0.99
- QC levels: LLOQ, low (3×LLOQ), mid, high (75% ULOQ); ≥4 of 6 QCs pass
- Within-run precision: CV ≤15%; between-run: CV ≤15%; LLOQ: CV ≤20%
- Selectivity, matrix effect, recovery, stability: per FDA 2018 guidance

UNITS AND CONVERSIONS:
- Always specify units explicitly
- Common: ng/mL = µg/L; AUC in ng·h/mL; CL in L/h or mL/min/kg; Vd in L or L/kg
- Concentration × volume = amount (mass)
- 1 µM = MW (g/mol) × 10⁻³ mg/L (for MW=400: 1 µM = 0.4 mg/L = 400 ng/mL)

WHEN IN DOUBT:
- Default to conservative assumptions (worst-case for safety assessment)
- Report uncertainty and recommend confirmatory experiments
- Always check whether linear PK assumptions hold before applying simple models
- Ask: is this a substrate, inhibitor, inducer, or all three?
"""


class PKPDAgent(A1):
    """Biomni A1 agent pre-configured for PKPD/DMPK/bioanalytical workflows.

    Automatically registers all PKPD tool modules and injects pharmacometric
    domain knowledge into the system prompt context.

    Usage
    -----
    >>> from biomni.agent.pkpd_agent import PKPDAgent
    >>> agent = PKPDAgent(
    ...     path="./data",
    ...     llm="claude-sonnet-4-20250514",
    ...     source="Anthropic",
    ... )
    >>> agent.go("Run NCA on this PK dataset and classify clearance")
    """

    PKPD_TOOL_MODULES = [
        "biomni.tool.dmpk",
        "biomni.tool.poppk",
        "biomni.tool.pbpk",
        "biomni.tool.bioanalytical",
        "biomni.tool.cdisc_io",
    ]

    def __init__(
        self,
        path: str | None = None,
        llm: str | None = None,
        source=None,
        use_tool_retriever: bool | None = None,
        timeout_seconds: int | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        commercial_mode: bool | None = None,
        expected_data_lake_files: list | None = None,
    ):
        super().__init__(
            path=path,
            llm=llm,
            source=source,
            use_tool_retriever=use_tool_retriever,
            timeout_seconds=timeout_seconds or 1800,
            base_url=base_url,
            api_key=api_key,
            commercial_mode=commercial_mode,
            expected_data_lake_files=expected_data_lake_files,
        )
        self._register_pkpd_tools()
        self._inject_pkpd_context()
        # Prepend pharmacometric domain rules to the system prompt.
        # system_prompt is read by reference in the LangGraph generate node,
        # so patching here takes effect on every subsequent agent.go() call.
        self.system_prompt = PKPD_SYSTEM_CONTEXT + "\n\n" + self.system_prompt
        print("🔬 PKPDAgent ready — DMPK, NCA, PopPK, PBPK, and bioanalytical tools loaded.")

    def _register_pkpd_tools(self):
        """Register all PKPD tool modules with the agent's tool registry."""
        import importlib
        import inspect

        for module_path in self.PKPD_TOOL_MODULES:
            try:
                module = importlib.import_module(module_path)
                module_name = module_path.split(".")[-1]
                functions = [
                    obj for name, obj in inspect.getmembers(module, inspect.isfunction)
                    if not name.startswith("_") and obj.__module__ == module_path
                ]
                added = 0
                for fn in functions:
                    try:
                        self.add_tool(fn)
                        added += 1
                    except Exception:
                        pass
                print(f"  ✓ {module_name}: {added} tools registered")
            except ImportError as exc:
                print(f"  ⚠ Could not import {module_path}: {exc}")

    def _inject_pkpd_context(self):
        """Inject pharmacometric domain knowledge into the know-how loader."""
        pkpd_knowhow_dir = os.path.join(
            os.path.dirname(__file__), "..", "know_how", "pkpd"
        )
        pkpd_knowhow_dir = os.path.normpath(pkpd_knowhow_dir)

        if os.path.exists(pkpd_knowhow_dir):
            try:
                extra_loader = KnowHowLoader(know_how_dir=pkpd_knowhow_dir)
                self.know_how_loader.documents.update(extra_loader.documents)
                print(f"  ✓ PKPD know-how: {len(extra_loader.documents)} documents loaded")
            except Exception as exc:
                print(f"  ⚠ Could not load PKPD know-how: {exc}")

    def run_nca_workflow(self, dataset_path: str, output_dir: str = "./nca_output") -> str:
        """High-level NCA workflow: validate dataset → run NCA → interpret results."""
        prompt = f"""
        Run a complete non-compartmental analysis workflow on the PK dataset at {dataset_path}.

        Step 1: Validate the dataset structure using validate_pk_dataset_for_nonmem.
        Step 2: For each subject, run NCA using run_nca to compute AUClast, AUCinf,
                Cmax, Tmax, t½, CL/F (or CL), and Vd/F (or Vd).
        Step 3: Summarise the geometric mean ± CV% for all PK parameters across subjects.
        Step 4: Classify the compound clearance (high/medium/low).
        Step 5: Flag any subjects with poor lambda-z fit (R² < 0.9) or AUC
                extrapolation > 20%.

        Save all outputs to {output_dir}.
        Provide a clear written summary suitable for a PK study report.
        """
        return self.go(prompt)

    def run_poppk_workflow(
        self,
        dataset_path: str,
        model_type: str = "2cmt_oral",
        output_dir: str = "./poppk_output",
    ) -> str:
        """High-level population PK workflow: fit → diagnose → simulate."""
        prompt = f"""
        Run a complete population PK analysis on dataset {dataset_path}.

        Step 1: Validate the dataset with validate_pk_dataset_for_nonmem.
        Step 2: Fit a {model_type} model using run_nlmixr2_model with SAEM estimation.
        Step 3: Generate and interpret GOF plots (DV vs PRED/IPRED, CWRES vs TIME/PRED).
        Step 4: Run a visual predictive check (VPC) and assess it.
        Step 5: Report fixed effects (with RSE%), IIV (% CV), and residual error.
        Step 6: Simulate the population PK profile at the studied dose using run_mrgsolve_simulation.
        Step 7: Provide a model development summary with key findings.

        Save outputs to {output_dir}. Apply FDA popPK guidance criteria throughout.
        """
        return self.go(prompt)

    def run_dmpk_panel(
        self,
        compound_name: str,
        microsomal_data: dict = None,
        ppb_data: dict = None,
        permeability_data: dict = None,
        cyp_data: dict = None,
        cmax_uM: float = None,
    ) -> str:
        """High-level DMPK panel analysis: stability, PPB, permeability, CYP, DDI."""
        prompt = f"""
        Perform a complete DMPK panel analysis for {compound_name}.

        Available data:
        - Microsomal stability: {microsomal_data}
        - Plasma protein binding: {ppb_data}
        - Caco-2 permeability: {permeability_data}
        - CYP inhibition IC50s: {cyp_data}
        - Clinical Cmax: {cmax_uM} µM

        Steps:
        1. Run calculate_microsomal_stability if microsomal data provided.
        2. Run calculate_plasma_protein_binding if PPB data provided.
        3. Run calculate_permeability if Caco-2 data provided.
        4. Run fit_cyp_inhibition for each CYP if IC50 data provided.
        5. If Cmax provided, run predict_ddi_risk_static to assess DDI risk.
        6. Run ivive_clearance to predict in vivo human hepatic clearance.
        7. Run summarise_adme_profile to generate the overall ADME summary.

        Provide a comprehensive DMPK assessment report with recommendations for
        the next experimental steps and any red flags for progression.
        """
        return self.go(prompt)

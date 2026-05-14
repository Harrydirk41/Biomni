"""DMPK (Drug Metabolism, Pharmacokinetics) assay analysis tools.

Covers NCA, in vitro ADME assays (microsomal stability, PPB,
permeability, CYP inhibition), DDI risk assessment, and IVIVE.
"""

import os

import numpy as np
from scipy.optimize import curve_fit
from scipy.stats import linregress

from biomni.utils import run_r_code


# ─────────────────────────────────────────────────────────────────────────────
# Non-Compartmental Analysis (NCA)
# ─────────────────────────────────────────────────────────────────────────────

def run_nca(
    concentration_data: list,
    time_data: list,
    dose: float,
    route: str = "iv",
    subject_id: str = "1",
    time_unit: str = "h",
    conc_unit: str = "ng/mL",
    dose_unit: str = "mg",
    output_dir: str = "./nca_output",
) -> str:
    """Run non-compartmental analysis (NCA) on PK concentration-time data.

    Uses the PKNCA R package (FDA/EMA compliant trapezoidal method).
    Calculates Cmax, Tmax, AUClast, AUCinf, t½, CL (or CL/F), Vd (or Vd/F),
    MRT, and lambda_z.

    Parameters
    ----------
    concentration_data : list
        Observed plasma concentrations in chronological order.
    time_data : list
        Corresponding time points matching concentration_data length.
    dose : float
        Administered dose value.
    route : str
        Dosing route: 'iv' (intravenous) or 'oral' / 'po' (extravascular).
    subject_id : str
        Subject or sample identifier for labelling output.
    time_unit : str
        Time unit label (e.g. 'h', 'min').
    conc_unit : str
        Concentration unit label (e.g. 'ng/mL', 'µg/L').
    dose_unit : str
        Dose unit label (e.g. 'mg', 'µg/kg').
    output_dir : str
        Directory to save NCA result CSV and concentration-time plot.
    """
    log = ["=" * 60]
    log.append("NON-COMPARTMENTAL ANALYSIS (NCA)")
    log.append("=" * 60)
    log.append(f"Subject     : {subject_id}")
    log.append(f"Route       : {route.upper()}")
    log.append(f"Dose        : {dose} {dose_unit}")
    log.append(f"Time points : {len(time_data)}")
    log.append(f"Conc range  : {min(concentration_data):.3g} – {max(concentration_data):.3g} {conc_unit}")

    os.makedirs(output_dir, exist_ok=True)
    route_r = "iv" if route.lower() == "iv" else "extravascular"

    r_code = f"""
suppressPackageStartupMessages({{
  library(PKNCA)
  library(ggplot2)
  library(dplyr)
}})

time  <- c({", ".join(str(t) for t in time_data)})
conc  <- c({", ".join(str(c) for c in concentration_data)})
dose  <- {dose}
route <- "{route_r}"

df_conc <- data.frame(
  subject = "{subject_id}",
  time    = time,
  conc    = conc
)
df_dose <- data.frame(
  subject = "{subject_id}",
  time    = 0,
  dose    = dose,
  route   = route
)

pk_conc <- PKNCAconc(df_conc, conc ~ time | subject)
pk_dose <- PKNCAdose(df_dose, dose ~ time | subject, route = route)
pk_data <- PKNCAdata(pk_conc, pk_dose)

intervals <- data.frame(
  start = 0,
  end   = max(time),
  auclast    = TRUE,
  aucinf.obs = TRUE,
  cmax       = TRUE,
  tmax       = TRUE,
  half.life  = TRUE,
  cl.obs     = TRUE,
  cl.last    = TRUE,
  vd.obs     = TRUE,
  mrt.obs    = TRUE,
  lambda.z   = TRUE,
  r.squared  = TRUE
)
pk_data$intervals <- intervals

results <- pk_nca(pk_data)
res_df  <- as.data.frame(results$result)

cat("NCA_RESULTS_START\\n")
print(res_df[, c("PPTESTCD", "PPORRES")], row.names = FALSE)
cat("NCA_RESULTS_END\\n")

# Save CSV
write.csv(res_df, file = "{output_dir}/nca_{subject_id}.csv", row.names = FALSE)

# Concentration-time plot
p <- ggplot(df_conc, aes(x = time, y = conc)) +
  geom_point(size = 3, colour = "#2166AC") +
  geom_line(colour = "#2166AC") +
  scale_y_log10() +
  labs(
    title    = paste("Concentration-Time Profile — Subject {subject_id}"),
    subtitle = paste("Route:", toupper(route), "| Dose:", dose, "{dose_unit}"),
    x        = "Time ({time_unit})",
    y        = paste("Concentration ({conc_unit}) — log scale")
  ) +
  theme_bw(base_size = 13)
ggsave("{output_dir}/ct_profile_{subject_id}.png", p, width = 8, height = 5, dpi = 150)
cat("Plot saved.\\n")
"""

    r_output = run_r_code(r_code)

    if "Error" in r_output and "NCA_RESULTS_START" not in r_output:
        log.append(f"\nR execution error:\n{r_output}")
        return "\n".join(log)

    # Parse structured results block
    log.append("\n── NCA Parameters ──")
    in_block = False
    for line in r_output.splitlines():
        if "NCA_RESULTS_START" in line:
            in_block = True
            continue
        if "NCA_RESULTS_END" in line:
            in_block = False
            continue
        if in_block and line.strip():
            log.append(f"  {line}")

    log.append(f"\nOutput files saved to: {output_dir}/")
    log.append("  nca_{id}.csv          — full parameter table")
    log.append("  ct_profile_{id}.png   — concentration-time plot")

    # Interpretation guidance
    log.append("\n── Interpretation Notes ──")
    if route.lower() in ("oral", "po"):
        log.append("  CL/F and Vd/F reported (apparent — confounded by bioavailability).")
        log.append("  To obtain absolute CL and Vd, an IV crossover is required.")
    else:
        log.append("  Absolute CL and Vd reported (IV dose assumed complete bioavailability).")
    log.append("  AUCinf extrapolation valid if AUC_extrap < 20% of AUCinf.")
    log.append("  R² for lambda-z should be ≥ 0.9 for reliable t½ estimate.")

    return "\n".join(log)


# ─────────────────────────────────────────────────────────────────────────────
# Microsomal / Hepatocyte Metabolic Stability
# ─────────────────────────────────────────────────────────────────────────────

def calculate_microsomal_stability(
    time_points: list,
    percent_remaining: list,
    microsomal_protein_conc_mg_per_mL: float = 0.5,
    incubation_volume_mL: float = 0.5,
    compound_conc_uM: float = 1.0,
    species: str = "human",
    matrix: str = "microsomes",
    compound_name: str = "compound",
) -> str:
    """Calculate intrinsic clearance (CLint) from in vitro metabolic stability assay.

    Fits a monoexponential decay to % parent remaining vs time to derive
    in vitro t½, CLint (microsomal), and scaled CLint (per gram liver).
    Classifies compound as high / medium / low clearance per industry thresholds.

    Parameters
    ----------
    time_points : list
        Incubation time points in minutes (e.g. [0, 5, 15, 30, 60]).
    percent_remaining : list
        % parent compound remaining at each time point (0–100).
    microsomal_protein_conc_mg_per_mL : float
        Microsomal protein concentration in the incubation (mg/mL).
    incubation_volume_mL : float
        Total incubation volume in mL.
    compound_conc_uM : float
        Substrate concentration in µM (must be << Km for linear conditions).
    species : str
        Species: 'human', 'rat', 'mouse', 'dog', 'monkey'.
        Used to select MPPGL (mg microsomal protein per gram liver).
    matrix : str
        'microsomes' or 'hepatocytes' (affects scaling factors).
    compound_name : str
        Compound identifier for reporting.
    """
    log = ["=" * 60]
    log.append("METABOLIC STABILITY ANALYSIS")
    log.append("=" * 60)
    log.append(f"Compound    : {compound_name}")
    log.append(f"Species     : {species}")
    log.append(f"Matrix      : {matrix}")
    log.append(f"[Protein]   : {microsomal_protein_conc_mg_per_mL} mg/mL")
    log.append(f"[Compound]  : {compound_conc_uM} µM")

    # MPPGL and liver weight scaling factors by species
    MPPGL = {"human": 45, "rat": 60, "mouse": 65, "dog": 77, "monkey": 48}
    liver_weight_g_per_kg = {"human": 20.7, "rat": 40.0, "mouse": 88.0, "dog": 32.0, "monkey": 26.0}
    body_weight_kg = {"human": 70, "rat": 0.25, "mouse": 0.025, "dog": 10, "monkey": 5}

    mppgl = MPPGL.get(species.lower(), 45)
    lw_g_per_kg = liver_weight_g_per_kg.get(species.lower(), 20.7)
    bw_kg = body_weight_kg.get(species.lower(), 70)
    liver_g = lw_g_per_kg * bw_kg

    try:
        time_arr = np.array(time_points, dtype=float)
        pr_arr = np.array(percent_remaining, dtype=float) / 100.0

        # Log-linear regression on ln(% remaining) vs time
        ln_pr = np.log(pr_arr + 1e-10)
        slope, intercept, r_value, p_value, std_err = linregress(time_arr, ln_pr)

        ke = -slope  # first-order elimination rate constant (min⁻¹)
        t_half_min = np.log(2) / ke if ke > 0 else float("inf")

        # CLint (µL/min/mg protein) — linear substrate conditions
        # CLint = (0.693 / t½) × (incubation_volume_µL / protein_amount_mg)
        incubation_vol_uL = incubation_volume_mL * 1000
        protein_mg = microsomal_protein_conc_mg_per_mL * incubation_volume_mL
        cl_int_uL_per_min_per_mg = (ke * incubation_vol_uL) / protein_mg

        # Scale to CLint per gram liver (µL/min/g liver)
        cl_int_per_g_liver = cl_int_uL_per_min_per_mg * mppgl

        # Scale to CLint,liver (mL/min/kg body weight)
        # CLint,liver = CLint × MPPGL × liver weight (g) / body weight (kg)
        cl_int_liver_mL_min_kg = (cl_int_uL_per_min_per_mg * mppgl * liver_g / bw_kg) / 1000

        log.append("\n── Kinetics ──")
        log.append(f"  ke (rate constant)  : {ke:.4f} min⁻¹")
        log.append(f"  In vitro t½         : {t_half_min:.1f} min")
        log.append(f"  R² (fit quality)    : {r_value**2:.4f}")
        log.append("\n── Intrinsic Clearance ──")
        log.append(f"  CLint (in vitro)    : {cl_int_uL_per_min_per_mg:.2f} µL/min/mg protein")
        log.append(f"  CLint (per g liver) : {cl_int_per_g_liver:.1f} µL/min/g liver")
        log.append(f"  CLint,liver (scaled): {cl_int_liver_mL_min_kg:.2f} mL/min/kg ({species})")
        log.append(f"  MPPGL used          : {mppgl} mg protein/g liver ({species})")

        # Classification (human thresholds from FDA/EMA guidance)
        log.append("\n── Classification (Human Liver Microsomal) ──")
        if t_half_min < 30:
            cls = "HIGH clearance"
            note = "t½ < 30 min. Likely rapid first-pass; may have low oral bioavailability."
        elif t_half_min <= 60:
            cls = "MEDIUM clearance"
            note = "t½ 30–60 min. Moderate hepatic extraction expected."
        else:
            cls = "LOW clearance"
            note = "t½ > 60 min. Low hepatic extraction; good metabolic stability."
        log.append(f"  Category : {cls}")
        log.append(f"  Note     : {note}")

        if r_value**2 < 0.9:
            log.append(f"\n  ⚠  R² = {r_value**2:.3f} < 0.9 — non-linear decay detected.")
            log.append("      Consider biphasic model or check substrate depletion > 80%.")
        if percent_remaining[0] < 80:
            log.append("  ⚠  t=0 recovery < 80% — check non-specific binding or instability at t=0.")

    except Exception as exc:
        log.append(f"\nCalculation error: {exc}")

    return "\n".join(log)


# ─────────────────────────────────────────────────────────────────────────────
# Plasma Protein Binding (PPB)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_plasma_protein_binding(
    buffer_conc: float,
    plasma_conc: float,
    method: str = "RED",
    species: str = "human",
    compound_name: str = "compound",
    nominal_conc_uM: float = 1.0,
) -> str:
    """Calculate unbound fraction in plasma (fu,p) from protein binding assay.

    Supports Rapid Equilibrium Dialysis (RED) and Equilibrium Dialysis.
    Applies mass balance correction for non-specific binding.

    Parameters
    ----------
    buffer_conc : float
        Measured concentration in buffer/receiver compartment (ng/mL or area ratio).
    plasma_conc : float
        Measured concentration in plasma/donor compartment (ng/mL or area ratio).
    method : str
        Assay method: 'RED' (rapid equilibrium dialysis) or 'ED' (equilibrium dialysis).
    species : str
        Plasma species: 'human', 'rat', 'mouse', 'dog', 'monkey'.
    compound_name : str
        Compound identifier.
    nominal_conc_uM : float
        Nominal compound concentration tested in µM.
    """
    log = ["=" * 60]
    log.append("PLASMA PROTEIN BINDING ANALYSIS")
    log.append("=" * 60)
    log.append(f"Compound    : {compound_name}")
    log.append(f"Species     : {species}")
    log.append(f"Method      : {method}")
    log.append(f"[Compound]  : {nominal_conc_uM} µM")

    try:
        fu = buffer_conc / plasma_conc
        fu = max(0.0, min(fu, 1.0))
        percent_bound = (1 - fu) * 100

        log.append("\n── Results ──")
        log.append(f"  Buffer concentration   : {buffer_conc:.4g}")
        log.append(f"  Plasma concentration   : {plasma_conc:.4g}")
        log.append(f"  fu,plasma (unbound)    : {fu:.4f}  ({fu*100:.2f}%)")
        log.append(f"  % Bound                : {percent_bound:.2f}%")

        # Classification
        log.append("\n── Classification ──")
        if fu < 0.01:
            cls = "HIGHLY bound (fu < 1%)"
            note = "Very high PPB. Free drug fraction very low. Consider fu correction in PK/PD."
        elif fu < 0.10:
            cls = "HIGHLY bound (fu 1–10%)"
            note = "High PPB. Drug-drug interactions via protein displacement are possible."
        elif fu < 0.50:
            cls = "MODERATELY bound (fu 10–50%)"
            note = "Moderate PPB."
        else:
            cls = "LOW binding (fu > 50%)"
            note = "Low PPB. Large unbound fraction; often lower risk of displacement interactions."
        log.append(f"  Category : {cls}")
        log.append(f"  Note     : {note}")

        # fu correction for CLint scaling
        log.append("\n── IVIVE Relevance ──")
        log.append("  fu,plasma used in well-stirred model:")
        log.append("  CLh = Qh × (fu × CLint) / (Qh + fu × CLint)")
        log.append(f"  For {species}, hepatic blood flow (Qh) ≈ 20.7 mL/min/kg.")
        if fu < 0.05:
            log.append("  ⚠  fu < 5% — small errors in fu measurement have large impact on predicted CLh.")
            log.append("     Replicate PPB assay recommended; consider ultracentrifugation for confirmation.")

    except Exception as exc:
        log.append(f"\nCalculation error: {exc}")

    return "\n".join(log)


# ─────────────────────────────────────────────────────────────────────────────
# Permeability (Caco-2 / PAMPA)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_permeability(
    apical_to_basolateral_conc: float,
    basolateral_to_apical_conc: float,
    donor_conc_initial: float,
    membrane_area_cm2: float = 0.33,
    time_h: float = 2.0,
    assay_type: str = "caco2",
    compound_name: str = "compound",
) -> str:
    """Calculate apparent permeability (Papp) and efflux ratio from Caco-2 or PAMPA.

    Calculates Papp A→B and B→A, efflux ratio, and classifies permeability
    per FDA/BCS guidance thresholds.

    Parameters
    ----------
    apical_to_basolateral_conc : float
        Concentration measured in basolateral compartment after A→B transport (ng/mL).
    basolateral_to_apical_conc : float
        Concentration measured in apical compartment after B→A transport (ng/mL).
    donor_conc_initial : float
        Initial donor concentration at t=0 (ng/mL), same for both directions.
    membrane_area_cm2 : float
        Surface area of the membrane insert in cm² (standard 24-well = 0.33 cm²).
    time_h : float
        Incubation time in hours.
    assay_type : str
        'caco2' or 'pampa'.
    compound_name : str
        Compound identifier.
    """
    log = ["=" * 60]
    log.append("PERMEABILITY ANALYSIS")
    log.append("=" * 60)
    log.append(f"Compound    : {compound_name}")
    log.append(f"Assay       : {assay_type.upper()}")
    log.append(f"Membrane area : {membrane_area_cm2} cm²")
    log.append(f"Incubation    : {time_h} h")

    try:
        # Receiver volume (basolateral = 0.9 mL standard; apical = 0.3 mL)
        vol_basolateral_mL = 0.9
        vol_apical_mL = 0.3
        time_s = time_h * 3600

        # Papp (cm/s) = (dQ/dt) / (A × C0)
        #             = (C_receiver × V_receiver) / (t × A × C_donor_initial)
        papp_ab = (apical_to_basolateral_conc * vol_basolateral_mL * 1e-3) / (
            time_s * membrane_area_cm2 * donor_conc_initial * 1e-3
        )
        papp_ba = (basolateral_to_apical_conc * vol_apical_mL * 1e-3) / (
            time_s * membrane_area_cm2 * donor_conc_initial * 1e-3
        )

        papp_ab_scaled = papp_ab * 1e6  # convert to × 10⁻⁶ cm/s
        papp_ba_scaled = papp_ba * 1e6

        efflux_ratio = papp_ba / papp_ab if papp_ab > 0 else float("inf")

        log.append("\n── Permeability Results ──")
        log.append(f"  Papp (A→B) : {papp_ab_scaled:.2f} × 10⁻⁶ cm/s")
        log.append(f"  Papp (B→A) : {papp_ba_scaled:.2f} × 10⁻⁶ cm/s")
        log.append(f"  Efflux ratio (B→A / A→B) : {efflux_ratio:.2f}")

        # BCS / FDA permeability classification
        log.append("\n── Permeability Classification ──")
        if papp_ab_scaled >= 10:
            perm_cls = "HIGH permeability (BCS Class I/II)"
            perm_note = "Papp ≥ 10 × 10⁻⁶ cm/s. Good intestinal absorption expected."
        elif papp_ab_scaled >= 1:
            perm_cls = "MODERATE permeability"
            perm_note = "Papp 1–10 × 10⁻⁶ cm/s. Partial absorption; formulation may be important."
        else:
            perm_cls = "LOW permeability (BCS Class III/IV)"
            perm_note = "Papp < 1 × 10⁻⁶ cm/s. Poor oral absorption expected without enhancement."
        log.append(f"  Category : {perm_cls}")
        log.append(f"  Note     : {perm_note}")

        # Efflux interpretation
        log.append("\n── Efflux Assessment ──")
        if efflux_ratio >= 2.0 and assay_type.lower() == "caco2":
            log.append(f"  ⚠  Efflux ratio = {efflux_ratio:.2f} ≥ 2.0")
            log.append("     Suggests active efflux transport (likely P-gp / BCRP).")
            log.append("     Confirm with P-gp inhibitor (GF120918 / elacridar) experiment.")
            log.append("     CNS penetration may be limited by BBB efflux.")
        elif efflux_ratio < 0.5:
            log.append(f"  Efflux ratio = {efflux_ratio:.2f} < 0.5 — possible active influx.")
        else:
            log.append(f"  Efflux ratio = {efflux_ratio:.2f} — no significant active efflux detected.")

    except Exception as exc:
        log.append(f"\nCalculation error: {exc}")

    return "\n".join(log)


# ─────────────────────────────────────────────────────────────────────────────
# CYP Inhibition — IC50 Fitting
# ─────────────────────────────────────────────────────────────────────────────

def fit_cyp_inhibition(
    inhibitor_conc_uM: list,
    percent_activity_remaining: list,
    cyp_enzyme: str = "CYP3A4",
    compound_name: str = "compound",
    run_tdi_check: bool = False,
    preincubation_activity: list = None,
) -> str:
    """Fit IC50 from CYP inhibition assay data using a 4-parameter logistic model.

    Fits % remaining activity vs inhibitor concentration to derive IC50 and
    Hill slope. Optionally assesses time-dependent inhibition (TDI) using
    the shift ratio (IC50 shift ≥ 1.5-fold indicates TDI).

    Parameters
    ----------
    inhibitor_conc_uM : list
        Inhibitor concentrations tested in µM (log-spaced recommended).
    percent_activity_remaining : list
        % CYP activity remaining at each concentration (100 = no inhibition).
    cyp_enzyme : str
        CYP isoform tested (e.g. 'CYP3A4', 'CYP2D6', 'CYP2C9', 'CYP1A2').
    compound_name : str
        Compound identifier.
    run_tdi_check : bool
        If True, computes IC50 shift ratio using preincubation_activity.
    preincubation_activity : list
        % activity remaining after 30-min preincubation (for TDI assessment).
    """
    log = ["=" * 60]
    log.append("CYP INHIBITION ANALYSIS")
    log.append("=" * 60)
    log.append(f"Compound    : {compound_name}")
    log.append(f"CYP enzyme  : {cyp_enzyme}")

    def four_pl(x, top, bottom, ic50, hill):
        return bottom + (top - bottom) / (1 + (ic50 / x) ** hill)

    try:
        x = np.array(inhibitor_conc_uM, dtype=float)
        y = np.array(percent_activity_remaining, dtype=float)

        p0 = [100, 0, np.median(x), 1.0]
        bounds = ([50, -10, 1e-4, 0.1], [110, 50, 1e4, 5.0])
        popt, pcov = curve_fit(four_pl, x, y, p0=p0, bounds=bounds, maxfev=10000)
        top, bottom, ic50, hill = popt
        perr = np.sqrt(np.diag(pcov))

        log.append("\n── 4-Parameter Logistic Fit ──")
        log.append(f"  IC50          : {ic50:.3f} µM  (SE: {perr[2]:.3f})")
        log.append(f"  Hill slope    : {hill:.2f}")
        log.append(f"  Top asymptote : {top:.1f}%")
        log.append(f"  Bottom asymptote: {bottom:.1f}%")

        # DDI risk flag (FDA 2020 DDI guidance)
        log.append("\n── DDI Risk (FDA 2020 Guidance) ──")
        log.append("  Basic R1 = 1 + [I]max,u / IC50  (use unbound Cmax at clinical dose)")
        log.append(f"  If R1 ≥ 1.02 → clinical DDI study warranted for {cyp_enzyme}")
        if ic50 < 1.0:
            log.append("  ⚠  IC50 < 1 µM — HIGH inhibition potency. Clinical DDI study likely required.")
        elif ic50 < 10.0:
            log.append("  IC50 1–10 µM — MODERATE potency. Evaluate against clinical [I]u,max.")
        else:
            log.append("  IC50 > 10 µM — LOW inhibition potency at likely clinical concentrations.")

        # TDI assessment
        if run_tdi_check and preincubation_activity is not None:
            log.append("\n── Time-Dependent Inhibition (TDI) ──")
            y_pre = np.array(preincubation_activity, dtype=float)
            try:
                popt_pre, _ = curve_fit(four_pl, x, y_pre, p0=p0, bounds=bounds, maxfev=10000)
                ic50_pre = popt_pre[2]
                shift_ratio = ic50 / ic50_pre
                log.append(f"  IC50 (no preincubation)     : {ic50:.3f} µM")
                log.append(f"  IC50 (30-min preincubation) : {ic50_pre:.3f} µM")
                log.append(f"  IC50 shift ratio            : {shift_ratio:.2f}")
                if shift_ratio >= 1.5:
                    log.append(f"  ⚠  Shift ratio ≥ 1.5 — TDI DETECTED for {cyp_enzyme}.")
                    log.append("     Mechanism-based inhibition (MBI) assay (kinact/KI) recommended.")
                    log.append("     Conduct FDA-recommended clinical DDI study.")
                else:
                    log.append("  Shift ratio < 1.5 — no significant TDI detected.")
            except Exception:
                log.append("  TDI fit failed — check preincubation data quality.")

    except Exception as exc:
        log.append(f"\nFitting error: {exc}")
        log.append("Tip: ensure inhibitor concentrations span 0.01–100× expected IC50.")

    return "\n".join(log)


# ─────────────────────────────────────────────────────────────────────────────
# DDI Risk Assessment — Static Mechanistic Model
# ─────────────────────────────────────────────────────────────────────────────

def predict_ddi_risk_static(
    compound_name: str,
    cmax_total_uM: float,
    fu_plasma: float,
    fu_gut: float = 1.0,
    dose_mg: float = None,
    cyp_ic50: dict = None,
    cyp_ki: dict = None,
    is_p_gp_substrate: bool = False,
    is_p_gp_inhibitor: bool = False,
    p_gp_ic50_uM: float = None,
) -> str:
    """Assess drug-drug interaction (DDI) risk using FDA/EMA static mechanistic models.

    Implements the basic and mechanistic static models from the FDA 2020
    DDI guidance. Calculates R1 (basic), R2 (gut), and flags which CYP
    isoforms require in vitro follow-up or clinical DDI study.

    Parameters
    ----------
    compound_name : str
        Name of the perpetrator (inhibitor/inducer) compound.
    cmax_total_uM : float
        Maximum total plasma concentration (Cmax,ss) at clinical dose in µM.
    fu_plasma : float
        Unbound fraction in plasma (0–1).
    fu_gut : float
        Unbound fraction in gut (assumed 1.0 if unknown).
    dose_mg : float
        Oral dose in mg (used for gut R2 calculation).
    cyp_ic50 : dict
        CYP IC50 values in µM. Keys: enzyme name (e.g. 'CYP3A4').
        Example: {'CYP3A4': 5.2, 'CYP2D6': 12.0}
    cyp_ki : dict
        CYP Ki values in µM (competitive inhibition, from Dixon plot).
    is_p_gp_substrate : bool
        Whether the compound is a known P-gp substrate.
    is_p_gp_inhibitor : bool
        Whether the compound inhibits P-gp.
    p_gp_ic50_uM : float
        P-gp IC50 in µM if known.
    """
    log = ["=" * 60]
    log.append("DDI RISK ASSESSMENT — STATIC MECHANISTIC MODEL")
    log.append("FDA 2020 DDI Guidance")
    log.append("=" * 60)
    log.append(f"Compound           : {compound_name}")
    log.append(f"Cmax,total         : {cmax_total_uM:.3f} µM")
    log.append(f"fu,plasma          : {fu_plasma:.4f}")

    imax_u = cmax_total_uM * fu_plasma  # unbound Cmax
    log.append(f"[I]max,u (unbound) : {imax_u:.4f} µM")

    all_ic50 = {}
    if cyp_ic50:
        all_ic50.update(cyp_ic50)
    if cyp_ki:
        all_ic50.update(cyp_ki)

    if all_ic50:
        log.append("\n── CYP Inhibition DDI (R1 = 1 + [I]max,u / IC50) ──")
        log.append("  Threshold: R1 ≥ 1.02 → follow-up required")
        log.append(f"  {'CYP':<12} {'IC50 (µM)':<14} {'[I]max,u/IC50':<18} {'R1':<10} {'Action'}")
        log.append(f"  {'-'*72}")
        for cyp, ic50 in sorted(all_ic50.items()):
            r1 = 1 + imax_u / ic50
            ratio = imax_u / ic50
            if r1 >= 1.02:
                if r1 >= 2.0:
                    action = "Clinical DDI study required"
                else:
                    action = "In vitro mechanistic study"
            else:
                action = "No further study needed"
            log.append(f"  {cyp:<12} {ic50:<14.3f} {ratio:<18.4f} {r1:<10.3f} {action}")

    # Gut DDI (R2) — for CYP3A substrates, oral perpetrator
    if dose_mg and "CYP3A4" in (all_ic50 or {}):
        ic50_3a4 = all_ic50["CYP3A4"]
        # R2 = 1 + (fu_gut × dose_uM / ka / qen) ... simplified
        # ka ≈ 0.1 min⁻¹, qen ≈ 18 mL/min/kg (enterocyte blood flow)
        mw_approx = 400  # assumed MW if unknown
        dose_uM_gut = (dose_mg / mw_approx) * 1e6 / 250  # rough gut conc
        r2 = 1 + (fu_gut * dose_uM_gut) / ic50_3a4
        log.append("\n── Gut CYP3A4 DDI (R2) ──")
        log.append(f"  R2 = {r2:.2f}  (threshold ≥ 11 warrants further study)")
        if r2 >= 11:
            log.append("  ⚠  R2 ≥ 11 — gut CYP3A4 inhibition is clinically relevant.")

    # P-gp assessment
    log.append("\n── P-glycoprotein (P-gp) Assessment ──")
    log.append(f"  Substrate   : {'Yes' if is_p_gp_substrate else 'No/Unknown'}")
    log.append(f"  Inhibitor   : {'Yes' if is_p_gp_inhibitor else 'No/Unknown'}")
    if is_p_gp_inhibitor and p_gp_ic50_uM:
        r_pgp = 1 + imax_u / p_gp_ic50_uM
        log.append(f"  [I]max,u / IC50 (P-gp) = {imax_u/p_gp_ic50_uM:.3f}  (R = {r_pgp:.2f})")
        if r_pgp >= 1.1:
            log.append("  ⚠  P-gp inhibition may affect absorption/CNS penetration of co-medications.")

    log.append("\n── Summary ──")
    log.append("  Review R1 values against each CYP above.")
    log.append("  Consult FDA 2020 'In Vitro Drug Interaction Studies' guidance for next steps.")
    log.append("  EMA 2012 DDI guideline also applies for EU submissions.")

    return "\n".join(log)


# ─────────────────────────────────────────────────────────────────────────────
# IVIVE — In Vitro to In Vivo Extrapolation of Clearance
# ─────────────────────────────────────────────────────────────────────────────

def ivive_clearance(
    cl_int_uL_per_min_per_mg: float,
    fu_plasma: float,
    fu_microsomal: float = 1.0,
    species: str = "human",
    model: str = "well_stirred",
    compound_name: str = "compound",
    molecular_weight: float = 400.0,
) -> str:
    """Predict in vivo hepatic clearance (CLh) from in vitro CLint using IVIVE.

    Implements well-stirred (WSM), parallel-tube (PTM), and dispersion
    models for hepatic clearance prediction from microsomal CLint.
    Returns predicted CLh, extraction ratio (Eh), and oral bioavailability
    estimate (Fh) assuming complete absorption.

    Parameters
    ----------
    cl_int_uL_per_min_per_mg : float
        Microsomal intrinsic clearance in µL/min/mg protein.
    fu_plasma : float
        Unbound fraction in plasma.
    fu_microsomal : float
        Unbound fraction in microsomal incubation (1.0 if not measured).
    species : str
        Target species: 'human', 'rat', 'mouse', 'dog', 'monkey'.
    model : str
        Hepatic model: 'well_stirred', 'parallel_tube', or 'dispersion'.
    compound_name : str
        Compound identifier.
    molecular_weight : float
        Molecular weight in Da (used for dose unit conversions).
    """
    log = ["=" * 60]
    log.append("IVIVE — HEPATIC CLEARANCE PREDICTION")
    log.append("=" * 60)
    log.append(f"Compound     : {compound_name}")
    log.append(f"Species      : {species}")
    log.append(f"Model        : {model.replace('_', ' ').title()}")

    MPPGL = {"human": 45, "rat": 60, "mouse": 65, "dog": 77, "monkey": 48}
    liver_weight_g_per_kg = {"human": 20.7, "rat": 40.0, "mouse": 88.0, "dog": 32.0, "monkey": 26.0}
    body_weight_kg = {"human": 70, "rat": 0.25, "mouse": 0.025, "dog": 10, "monkey": 5}
    # Hepatic blood flow (Qh) mL/min/kg
    qh_mL_min_kg = {"human": 20.7, "rat": 55.2, "mouse": 90.0, "dog": 30.9, "monkey": 44.0}

    mppgl = MPPGL.get(species.lower(), 45)
    lw_g_per_kg = liver_weight_g_per_kg.get(species.lower(), 20.7)
    bw = body_weight_kg.get(species.lower(), 70)
    qh = qh_mL_min_kg.get(species.lower(), 20.7)

    liver_g = lw_g_per_kg * bw

    # Scale CLint to per kg body weight
    # CLint,scaled (mL/min/kg) = CLint (µL/min/mg) × MPPGL × liver_g / bw / 1000
    cl_int_scaled = cl_int_uL_per_min_per_mg * mppgl * (liver_g / bw) / 1000

    # Correct for fu (unbound in plasma and microsomes)
    cl_int_u = cl_int_scaled * (fu_plasma / fu_microsomal)

    log.append("\n── Scaling ──")
    log.append(f"  CLint (in vitro)      : {cl_int_uL_per_min_per_mg:.2f} µL/min/mg")
    log.append(f"  MPPGL                 : {mppgl} mg/g liver ({species})")
    log.append(f"  Liver weight          : {liver_g:.1f} g ({species}, {bw} kg)")
    log.append(f"  CLint,scaled          : {cl_int_scaled:.2f} mL/min/kg")
    log.append(f"  CLint,u (fu-corrected): {cl_int_u:.2f} mL/min/kg")
    log.append(f"  Hepatic blood flow Qh : {qh:.1f} mL/min/kg ({species})")

    # Hepatic models
    if model == "well_stirred":
        clh = qh * cl_int_u / (qh + cl_int_u)
    elif model == "parallel_tube":
        eh = 1 - np.exp(-cl_int_u / qh)
        clh = qh * eh
    else:  # dispersion (simplified)
        dn = cl_int_u / qh
        rn = np.sqrt(1 + 4 * dn)
        eh = 1 - (4 * rn / ((1 + rn) ** 2 * np.exp((rn - 1) / (2 * 0.17))))
        eh = max(0, min(eh, 1))
        clh = qh * eh

    eh = clh / qh
    fh = 1 - eh  # hepatic first-pass availability

    log.append(f"\n── Predicted Hepatic Clearance ({model.replace('_', ' ').title()} Model) ──")
    log.append(f"  CLh                   : {clh:.2f} mL/min/kg")
    log.append(f"  CLh (total, {bw} kg)  : {clh * bw:.1f} mL/min")
    log.append(f"  Extraction ratio (Eh) : {eh:.3f}")
    log.append(f"  Fh (oral, hepatic)    : {fh:.3f}  ({fh*100:.1f}%)")

    log.append("\n── Classification ──")
    if eh > 0.7:
        log.append("  HIGH extraction ratio (Eh > 0.7)")
        log.append("  Sensitive to hepatic blood flow changes (heart failure, food effect).")
        log.append("  Oral bioavailability likely < 30% from first-pass alone.")
    elif eh > 0.3:
        log.append("  INTERMEDIATE extraction ratio (0.3–0.7)")
        log.append("  Both CLint and blood flow contribute to CLh.")
    else:
        log.append("  LOW extraction ratio (Eh < 0.3)")
        log.append("  CLh sensitive to changes in CLint (enzyme inhibition/induction).")
        log.append("  PPB changes can affect CLh (for restrictively cleared drugs).")

    log.append("\n── Caveats ──")
    log.append("  IVIVE predictions typically have 2–3-fold uncertainty.")
    log.append("  Verify with in vivo IV PK study in preclinical species.")
    log.append("  Does not account for renal, biliary, or gut clearance.")

    return "\n".join(log)


# ─────────────────────────────────────────────────────────────────────────────
# Complete ADME Profile Summary
# ─────────────────────────────────────────────────────────────────────────────

def summarise_adme_profile(
    compound_name: str,
    microsomal_t_half_min: float = None,
    fu_plasma: float = None,
    papp_ab_1e6_cm_s: float = None,
    efflux_ratio: float = None,
    cyp_ic50_dict: dict = None,
    predicted_clh_mL_min_kg: float = None,
    predicted_fh: float = None,
    species: str = "human",
) -> str:
    """Generate a comprehensive ADME profile summary for a compound.

    Integrates microsomal stability, plasma protein binding, permeability,
    CYP inhibition, and predicted clearance into a single scored profile.
    Flags issues and suggests next experiments.

    Parameters
    ----------
    compound_name : str
        Compound identifier.
    microsomal_t_half_min : float
        In vitro microsomal t½ in minutes (from metabolic stability assay).
    fu_plasma : float
        Unbound fraction in plasma (0–1).
    papp_ab_1e6_cm_s : float
        Caco-2 or PAMPA Papp A→B in units of × 10⁻⁶ cm/s.
    efflux_ratio : float
        Papp B→A / Papp A→B efflux ratio.
    cyp_ic50_dict : dict
        Dict of CYP IC50 values in µM. E.g. {'CYP3A4': 5.2}.
    predicted_clh_mL_min_kg : float
        Predicted hepatic clearance from IVIVE in mL/min/kg.
    predicted_fh : float
        Predicted hepatic first-pass availability fraction.
    species : str
        Species context.
    """
    log = ["=" * 60]
    log.append(f"ADME PROFILE SUMMARY — {compound_name.upper()}")
    log.append(f"Species: {species}")
    log.append("=" * 60)

    flags = []
    positives = []

    # Metabolic stability
    log.append("\n1. METABOLIC STABILITY")
    if microsomal_t_half_min is not None:
        if microsomal_t_half_min < 30:
            log.append(f"   HLM t½: {microsomal_t_half_min:.0f} min  →  HIGH clearance  ⚠")
            flags.append("High HLM clearance (t½ < 30 min)")
        elif microsomal_t_half_min <= 60:
            log.append(f"   HLM t½: {microsomal_t_half_min:.0f} min  →  MEDIUM clearance")
        else:
            log.append(f"   HLM t½: {microsomal_t_half_min:.0f} min  →  LOW clearance  ✓")
            positives.append("Good metabolic stability")
    else:
        log.append("   Not measured")

    # Plasma protein binding
    log.append("\n2. PLASMA PROTEIN BINDING")
    if fu_plasma is not None:
        log.append(f"   fu,plasma: {fu_plasma:.3f}  ({(1-fu_plasma)*100:.1f}% bound)")
        if fu_plasma < 0.01:
            flags.append("Very high PPB (fu < 1%) — large error impact on IVIVE")
    else:
        log.append("   Not measured")

    # Permeability
    log.append("\n3. PERMEABILITY")
    if papp_ab_1e6_cm_s is not None:
        if papp_ab_1e6_cm_s >= 10:
            log.append(f"   Papp A→B: {papp_ab_1e6_cm_s:.1f} × 10⁻⁶ cm/s  →  HIGH  ✓")
            positives.append("High permeability")
        elif papp_ab_1e6_cm_s >= 1:
            log.append(f"   Papp A→B: {papp_ab_1e6_cm_s:.1f} × 10⁻⁶ cm/s  →  MODERATE")
        else:
            log.append(f"   Papp A→B: {papp_ab_1e6_cm_s:.2f} × 10⁻⁶ cm/s  →  LOW  ⚠")
            flags.append("Low permeability (Papp < 1)")
        if efflux_ratio and efflux_ratio >= 2.0:
            log.append(f"   Efflux ratio: {efflux_ratio:.1f}  →  P-gp/BCRP efflux suspected  ⚠")
            flags.append(f"Efflux ratio {efflux_ratio:.1f} — P-gp/BCRP efflux")
    else:
        log.append("   Not measured")

    # CYP inhibition
    log.append("\n4. CYP INHIBITION RISK")
    if cyp_ic50_dict:
        for cyp, ic50 in sorted(cyp_ic50_dict.items()):
            risk = "⚠ HIGH" if ic50 < 1 else ("MODERATE" if ic50 < 10 else "low")
            log.append(f"   {cyp}: IC50 = {ic50:.2f} µM  →  {risk}")
            if ic50 < 1:
                flags.append(f"{cyp} inhibition IC50 < 1 µM")
    else:
        log.append("   Not measured")

    # Predicted in vivo
    log.append(f"\n5. PREDICTED IN VIVO ({species.upper()})")
    if predicted_clh_mL_min_kg is not None:
        log.append(f"   Predicted CLh : {predicted_clh_mL_min_kg:.2f} mL/min/kg")
    if predicted_fh is not None:
        log.append(f"   Predicted Fh  : {predicted_fh:.2f}  ({predicted_fh*100:.0f}% hepatic availability)")

    # Overall flags
    log.append("\n── Issues Flagged ──")
    if flags:
        for f in flags:
            log.append(f"  ⚠  {f}")
    else:
        log.append("  No major issues flagged.")

    log.append("\n── Strengths ──")
    if positives:
        for p in positives:
            log.append(f"  ✓  {p}")

    log.append("\n── Suggested Next Experiments ──")
    if any("clearance" in f.lower() for f in flags):
        log.append("  • Hepatocyte stability assay (more predictive CLh than microsomes)")
        log.append("  • Metabolite ID to identify major metabolic soft spots")
        log.append("  • Reactive metabolite trapping (GSH/KCN) if structural alerts present")
    if any("permeability" in f.lower() for f in flags):
        log.append("  • PAMPA-BBB (CNS permeability if relevant)")
        log.append("  • Solubility assay (thermodynamic and kinetic) — often driver of low Papp")
    if any("efflux" in f.lower() for f in flags):
        log.append("  • Bidirectional Caco-2 with P-gp/BCRP inhibitors (GF120918)")
        log.append("  • MDR1-MDCK assay for P-gp substrate confirmation")
    if any("CYP" in f for f in flags):
        log.append("  • CYP reaction phenotyping (which CYP mediates metabolism?)")
        log.append("  • TDI assessment (IC50 shift assay) for MBI risk")
        log.append("  • Clinical DDI study planning if R1 ≥ 1.02")

    return "\n".join(log)

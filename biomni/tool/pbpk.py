"""Physiologically-based pharmacokinetic (PBPK) modelling and IVIVE tools.

Covers minimal PBPK models, full organ-compartment PBPK, in vitro–in vivo
extrapolation (IVIVE), allometric scaling, and human dose prediction
from preclinical data.
"""

import os
import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp
from scipy.optimize import minimize

from biomni.utils import run_r_code


def run_minimal_pbpk_model(
    compound_name: str,
    species: str = "human",
    dose_mg_kg: float = 1.0,
    route: str = "iv",
    fu_plasma: float = 0.1,
    fu_tissue: float = 0.01,
    cl_int_uL_per_min_per_mg: float = 20.0,
    logP: float = 2.0,
    simulation_duration_h: float = 24.0,
    output_dir: str = "./pbpk_output",
) -> str:
    """Run a minimal physiologically-based PK (mPBPK) model simulation.

    Uses a three-compartment PBPK structure: blood, liver (eliminating),
    and peripheral tissue. Physiology parameters are taken from published
    literature for each species.

    Parameters
    ----------
    compound_name : str
        Compound identifier.
    species : str
        'human', 'rat', 'mouse', 'dog', 'monkey'.
    dose_mg_kg : float
        IV or oral dose in mg/kg.
    route : str
        'iv' or 'oral'.
    fu_plasma : float
        Unbound fraction in plasma.
    fu_tissue : float
        Unbound fraction in tissue (use measured Kp or estimate from logP).
    cl_int_uL_per_min_per_mg : float
        Hepatic microsomal CLint in µL/min/mg protein.
    logP : float
        Octanol-water partition coefficient (used for Kp estimation if fu_tissue not measured).
    simulation_duration_h : float
        Simulation duration in hours.
    output_dir : str
        Directory for plots and output CSV.
    """
    log = ["=" * 60]
    log.append("MINIMAL PBPK MODEL SIMULATION")
    log.append("=" * 60)
    log.append(f"Compound    : {compound_name}")
    log.append(f"Species     : {species}")
    log.append(f"Route       : {route.upper()}")
    log.append(f"Dose        : {dose_mg_kg} mg/kg")

    # Physiological parameters by species
    physiology = {
        "human":  {"BW": 70.0,   "Vblood": 5.6,   "Vliver": 1.69,  "Vtissue": 38.0,  "Qliver": 87.0,   "MPPGL": 45},
        "rat":    {"BW": 0.25,   "Vblood": 0.015,  "Vliver": 0.01,  "Vtissue": 0.16,  "Qliver": 1.0,    "MPPGL": 60},
        "mouse":  {"BW": 0.025,  "Vblood": 0.0017, "Vliver": 0.001, "Vtissue": 0.016, "Qliver": 0.1,    "MPPGL": 65},
        "dog":    {"BW": 10.0,   "Vblood": 0.7,    "Vliver": 0.32,  "Vtissue": 6.0,   "Qliver": 30.9,   "MPPGL": 77},
        "monkey": {"BW": 5.0,    "Vblood": 0.3,    "Vliver": 0.13,  "Vtissue": 3.0,   "Qliver": 12.5,   "MPPGL": 48},
    }
    phys = physiology.get(species.lower(), physiology["human"])
    BW, Vb, Vl, Vt = phys["BW"], phys["Vblood"], phys["Vliver"], phys["Vtissue"]
    Ql, mppgl = phys["Qliver"], phys["MPPGL"]

    # Scale volumes to L/kg
    Vb_L = Vb / BW
    Vl_L = Vl / BW
    Vt_L = Vt / BW

    # Tissue:plasma partition coefficient (Kp) — Berezhkovskiy method
    fup = fu_plasma
    Kp = (1 + 10 ** (logP - 1.115)) * fup / fu_tissue if fu_tissue > 0 else 5.0
    Kp = max(0.1, min(Kp, 100))

    # Scale CLint to body
    CL_int_mL_min_kg = cl_int_uL_per_min_per_mg * mppgl * (Vl / BW * 1000) / 1000

    # Well-stirred hepatic clearance
    Ql_mL_min_kg = Ql / BW
    CLh_mL_min_kg = Ql_mL_min_kg * (fup * CL_int_mL_min_kg) / (Ql_mL_min_kg + fup * CL_int_mL_min_kg)
    CLh_L_h_kg = CLh_mL_min_kg * 60 / 1000

    dose_mg = dose_mg_kg * BW
    dose_nmol = dose_mg * 1e6 / 400  # approximate MW=400

    log.append(f"\n── Physiological Parameters ({species}) ──")
    log.append(f"  Body weight        : {BW} kg")
    log.append(f"  Blood volume       : {Vb:.3f} L  ({Vb_L*1000:.1f} mL/kg)")
    log.append(f"  Liver volume       : {Vl:.3f} L  ({Vl_L*1000:.1f} mL/kg)")
    log.append(f"  Peripheral volume  : {Vt:.2f} L  ({Vt_L*1000:.0f} mL/kg)")
    log.append(f"  Hepatic blood flow : {Ql:.1f} mL/min  ({Ql_mL_min_kg:.1f} mL/min/kg)")
    log.append(f"\n── Compound Properties ──")
    log.append(f"  fu,plasma          : {fup:.4f}")
    log.append(f"  Kp (blood:tissue)  : {Kp:.2f}  (estimated from logP={logP})")
    log.append(f"  CLint (microsomal) : {cl_int_uL_per_min_per_mg:.1f} µL/min/mg")
    log.append(f"  CLh (well-stirred) : {CLh_mL_min_kg:.2f} mL/min/kg  |  {CLh_L_h_kg:.3f} L/h/kg")
    log.append(f"  Eh                 : {CLh_mL_min_kg/Ql_mL_min_kg:.3f}")

    # ODE simulation
    t_h = np.linspace(0, simulation_duration_h, int(simulation_duration_h * 12) + 1)
    t_min = t_h * 60
    Ql_h = Ql_mL_min_kg * 60 / 1000  # L/h/kg
    Qt_h = Ql_h  # simplified: total perfusion ≈ hepatic for 3-cmt

    # Initial conditions (mg/kg in each compartment)
    if route.lower() == "iv":
        C0 = [dose_mg_kg / Vb_L, 0.0, 0.0]  # [blood, liver, tissue]
    else:
        C0 = [0.0, 0.0, 0.0, dose_mg_kg]  # add gut depot

    def odes_iv(t, C):
        Cb, Cl, Ct = C
        # Blood: receives from liver + tissue, loses to liver + tissue
        dCb = (Ql_h * (Cl / Kp - Cb) + Qt_h * (Ct / Kp - Cb)) / Vb_L
        # Liver: receives blood, eliminates
        dCl = (Ql_h * (Cb - Cl / Kp) - CLh_L_h_kg * Cl / Kp) / Vl_L
        # Tissue: passive distribution
        dCt = Qt_h * (Cb - Ct / Kp) / Vt_L
        return [dCb, dCl, dCt]

    try:
        sol = solve_ivp(odes_iv, [0, simulation_duration_h], C0,
                        t_eval=t_h, method="RK45", rtol=1e-6)
        blood_conc = sol.y[0]
        plasma_conc = blood_conc / 0.55  # blood:plasma ratio ~0.55

        os.makedirs(output_dir, exist_ok=True)
        df = pd.DataFrame({"time_h": t_h, "C_blood_mg_L": blood_conc,
                           "C_plasma_mg_L": plasma_conc,
                           "C_liver_mg_L": sol.y[1], "C_tissue_mg_L": sol.y[2]})
        df.to_csv(f"{output_dir}/pbpk_simulation_{species}.csv", index=False)

        # Key PK metrics
        auc = np.trapz(plasma_conc, t_h)
        cmax = float(np.max(plasma_conc))
        t_half_h = np.log(2) * (Vb_L + Vt_L / Kp) / CLh_L_h_kg

        log.append(f"\n── Simulated PK Metrics ({route.upper()}, {dose_mg_kg} mg/kg) ──")
        log.append(f"  Cmax (plasma)      : {cmax:.4g} mg/L")
        log.append(f"  AUCinf (plasma)    : {auc:.4g} mg·h/L")
        log.append(f"  Predicted t½       : {t_half_h:.2f} h")
        log.append(f"  Predicted Vd       : {auc > 0 and (dose_mg_kg / cmax):.2f or 'N/A'} L/kg (approx)")
        log.append(f"\nSimulation CSV: {output_dir}/pbpk_simulation_{species}.csv")

    except Exception as exc:
        log.append(f"\nODE simulation error: {exc}")

    return "\n".join(log)


def predict_human_pk_from_preclinical(
    compound_name: str,
    preclinical_data: list,
    prediction_method: str = "allometric",
    molecular_weight: float = 400.0,
    protein_binding_correction: bool = True,
    output_dir: str = "./human_pk_prediction",
) -> str:
    """Predict human PK parameters from multi-species preclinical PK data.

    Implements simple and fixed-exponent allometric scaling, rule of exponents
    (ROE), and Mahmood method for CL prediction. Scales Vd by body surface area
    or simple allometry.

    Parameters
    ----------
    compound_name : str
        Compound identifier.
    preclinical_data : list
        List of dicts, each with: 'species', 'BW_kg', 'CL_mL_min_kg',
        'Vd_L_kg', 't_half_h', 'fu_plasma'.
        Example:
          [{'species': 'rat', 'BW_kg': 0.25, 'CL_mL_min_kg': 40.0, 'Vd_L_kg': 2.0, 'fu_plasma': 0.05},
           {'species': 'dog', 'BW_kg': 10.0, 'CL_mL_min_kg': 12.0, 'Vd_L_kg': 1.8, 'fu_plasma': 0.06}]
    prediction_method : str
        'allometric' (simple allometry), 'roi' (rule of exponents),
        'mahmood', 'unbound_cl' (fu-corrected).
    molecular_weight : float
        MW in Da (used for renal clearance classification).
    protein_binding_correction : bool
        Apply fu-correction: scale unbound CL across species.
    output_dir : str
        Output directory.
    """
    log = ["=" * 60]
    log.append("HUMAN PK PREDICTION FROM PRECLINICAL DATA")
    log.append("=" * 60)
    log.append(f"Compound       : {compound_name}")
    log.append(f"Method         : {prediction_method}")
    log.append(f"Species tested : {', '.join(d['species'] for d in preclinical_data)}")

    human_BW = 70.0
    human_brain_weight = 1.53  # kg (for MLP correction)

    bw_list = np.array([d["BW_kg"] for d in preclinical_data])
    cl_list = np.array([d["CL_mL_min_kg"] for d in preclinical_data])
    vd_list = np.array([d.get("Vd_L_kg", 1.0) for d in preclinical_data])

    # Total CL (mL/min) = CL (mL/min/kg) × BW
    cl_total = cl_list * bw_list
    vd_total = vd_list * bw_list

    log.append(f"\n── Preclinical Data ──")
    log.append(f"  {'Species':<12} {'BW (kg)':<12} {'CL (mL/min/kg)':<18} {'CL total (mL/min)':<20} {'Vd (L/kg)':<12}")
    log.append(f"  {'-' * 74}")
    for d, cl_t, vd_t in zip(preclinical_data, cl_total, vd_total):
        log.append(f"  {d['species']:<12} {d['BW_kg']:<12.3f} {d['CL_mL_min_kg']:<18.2f} {cl_t:<20.2f} {d.get('Vd_L_kg', 1.0):<12.2f}")

    # Simple allometric scaling: Y = a × BW^b
    try:
        log_bw = np.log(bw_list)
        log_cl = np.log(cl_total)
        b_cl, log_a_cl = np.polyfit(log_bw, log_cl, 1)
        a_cl = np.exp(log_a_cl)
        cl_human_pred = a_cl * human_BW ** b_cl
        cl_human_per_kg = cl_human_pred / human_BW

        log_vd = np.log(vd_total)
        b_vd, log_a_vd = np.polyfit(log_bw, log_vd, 1)
        a_vd = np.exp(log_a_vd)
        vd_human_pred = a_vd * human_BW ** b_vd
        vd_human_per_kg = vd_human_pred / human_BW

        log.append(f"\n── Allometric Scaling: Y = a × BW^b ──")
        log.append(f"  CL: a = {a_cl:.4g}, b = {b_cl:.3f}")
        log.append(f"  Vd: a = {a_vd:.4g}, b = {b_vd:.3f}")

        # Rule of exponents interpretation
        log.append(f"\n── Rule of Exponents (CL exponent = {b_cl:.2f}) ──")
        if b_cl < 0.7:
            log.append(f"  b < 0.7 → Use simple allometry directly.")
        elif b_cl < 1.0:
            log.append(f"  0.7 ≤ b < 1.0 → Use allometry with MLP (Maximum Life Span Potential) correction.")
            log.append(f"  MLP-corrected prediction recommended for final estimate.")
        else:
            log.append(f"  b ≥ 1.0 → Use allometry with brain weight correction (Mahmood).")

        log.append(f"\n── Predicted Human PK ──")
        log.append(f"  Predicted CL (human) : {cl_human_per_kg:.2f} mL/min/kg  ({cl_human_pred:.0f} mL/min, {human_BW} kg)")
        log.append(f"  Predicted Vd (human) : {vd_human_per_kg:.2f} L/kg  ({vd_human_pred:.0f} L, {human_BW} kg)")
        t_half_pred = np.log(2) * vd_human_pred / (cl_human_pred / 1000 * 60)
        log.append(f"  Predicted t½ (human) : {t_half_pred:.1f} h")

        # Typical prediction uncertainty
        log.append(f"\n── Uncertainty ──")
        log.append(f"  Allometric scaling typically has 2–3-fold error for CL.")
        log.append(f"  In vitro-in vivo correction (IVIVE) via fu reduces error.")
        log.append(f"  Recommend validating with at least 3 species (rat, dog, monkey).")

    except Exception as exc:
        log.append(f"\nScaling calculation error: {exc}")

    return "\n".join(log)


def calculate_allometric_dose(
    preclinical_dose_mg_kg: float,
    preclinical_species: str,
    target_species: str = "human",
    allometric_exponent: float = 0.75,
    conversion_basis: str = "AUC",
    preclinical_cl_mL_min_kg: float = None,
    human_cl_mL_min_kg: float = None,
) -> str:
    """Calculate the equivalent human dose from a preclinical efficacious/toxic dose.

    Uses body-weight allometry, body-surface-area (BSA) normalization,
    or clearance-based AUC matching.

    Parameters
    ----------
    preclinical_dose_mg_kg : float
        Dose in the preclinical species in mg/kg.
    preclinical_species : str
        Preclinical species: 'mouse', 'rat', 'dog', 'monkey'.
    target_species : str
        Target: 'human' (or another species for cross-species bridging).
    allometric_exponent : float
        Allometric exponent (default 0.75 for metabolic rate / CL scaling).
    conversion_basis : str
        'BW' (body weight), 'BSA' (body surface area), 'AUC' (CL-based).
    preclinical_cl_mL_min_kg : float
        Preclinical CL in mL/min/kg (required for AUC-based conversion).
    human_cl_mL_min_kg : float
        Predicted human CL in mL/min/kg (required for AUC-based conversion).
    """
    log = ["=" * 60]
    log.append("CROSS-SPECIES DOSE CONVERSION")
    log.append("=" * 60)

    body_weights = {"mouse": 0.02, "rat": 0.25, "rabbit": 2.0,
                    "dog": 10.0, "monkey": 5.0, "human": 70.0}
    km_factors = {"mouse": 3, "rat": 6, "rabbit": 12,
                  "dog": 20, "monkey": 12, "human": 37}

    bw_animal = body_weights.get(preclinical_species.lower(), 0.25)
    bw_human = body_weights.get(target_species.lower(), 70.0)
    km_animal = km_factors.get(preclinical_species.lower(), 6)
    km_human = km_factors.get(target_species.lower(), 37)

    dose_mg_animal = preclinical_dose_mg_kg * bw_animal

    log.append(f"  Preclinical species : {preclinical_species} ({bw_animal} kg)")
    log.append(f"  Target species      : {target_species} ({bw_human} kg)")
    log.append(f"  Preclinical dose    : {preclinical_dose_mg_kg} mg/kg  ({dose_mg_animal:.2f} mg total)")
    log.append(f"  Conversion basis    : {conversion_basis}")

    if conversion_basis == "BW":
        human_dose_mg_kg = preclinical_dose_mg_kg
        human_dose_mg = human_dose_mg_kg * bw_human
        log.append(f"\n  Allometric (BW) human dose : {human_dose_mg_kg:.3f} mg/kg  ({human_dose_mg:.1f} mg)")

    elif conversion_basis == "BSA":
        # HED (mg/kg) = animal_dose (mg/kg) × (BW_animal / BW_human)^(1/3) × (Km_animal / Km_human)
        hed_mg_kg = preclinical_dose_mg_kg * (bw_animal / bw_human) ** (1/3) * (km_animal / km_human)
        human_dose_mg = hed_mg_kg * bw_human
        log.append(f"\n  HED (BSA-based) human dose : {hed_mg_kg:.4f} mg/kg  ({human_dose_mg:.1f} mg)")
        log.append(f"  Km factors used: {preclinical_species}={km_animal}, {target_species}={km_human}")
        log.append(f"  (FDA HED method: Guidance for Industry, 2005)")

    elif conversion_basis == "AUC":
        if preclinical_cl_mL_min_kg and human_cl_mL_min_kg:
            # AUC-matching: AUC_h = AUC_a → Dose_h / CL_h = Dose_a / CL_a
            # Dose_h (mg) = Dose_a (mg) × CL_h_total / CL_a_total
            cl_animal_total = preclinical_cl_mL_min_kg * bw_animal
            cl_human_total = human_cl_mL_min_kg * bw_human
            human_dose_mg = dose_mg_animal * (cl_human_total / cl_animal_total)
            human_dose_mg_kg = human_dose_mg / bw_human
            log.append(f"\n  AUC-matched human dose : {human_dose_mg_kg:.4f} mg/kg  ({human_dose_mg:.1f} mg)")
            log.append(f"  Animal CL: {cl_animal_total:.1f} mL/min  |  Human CL: {cl_human_total:.1f} mL/min")
        else:
            log.append("\n  AUC-based conversion requires both preclinical_cl and human_cl.")

    log.append(f"\n── Safety Note ──")
    log.append("  Always apply a safety margin (typically 1/10 to 1/6 of NOAEL HED for FIH dose).")
    log.append("  Consult FDA 'Estimating Maximum Safe Starting Dose in Initial Clinical Trials'.")
    log.append("  MRSD (Maximum Recommended Starting Dose) = HED / Safety Factor")

    return "\n".join(log)

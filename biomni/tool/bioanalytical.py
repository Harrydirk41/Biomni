"""Bioanalytical method tools for LC-MS/MS, calibration curves,
QC assessment, and FDA/EMA method validation.
"""

import os
import numpy as np
import pandas as pd


def fit_calibration_curve(
    nominal_conc: list,
    peak_area_ratios: list,
    weighting: str = "1/x2",
    model: str = "linear",
    lloq_target: float = None,
    uloq_target: float = None,
    compound_name: str = "analyte",
    output_dir: str = "./bioanalytical_output",
) -> str:
    """Fit a calibration curve for bioanalytical LC-MS/MS quantification.

    Supports linear and quadratic regression with 1/x, 1/x², or unweighted
    fitting. Calculates back-calculated concentrations and % accuracy for
    all calibrators. Reports whether each standard meets FDA/EMA acceptance
    (≤15% bias at non-LLOQ; ≤20% at LLOQ).

    Parameters
    ----------
    nominal_conc : list
        Nominal concentrations of calibration standards (same units).
    peak_area_ratios : list
        Analyte:IS peak area ratios at each calibration level.
    weighting : str
        Weighting scheme: 'none', '1/x', '1/x2', '1/y', '1/y2'.
    model : str
        Regression model: 'linear' (y = mx + b) or 'quadratic' (y = ax² + bx + c).
    lloq_target : float
        Expected LLOQ concentration (lowest standard). Applies 20% bias limit.
    uloq_target : float
        Expected ULOQ concentration (highest standard).
    compound_name : str
        Analyte name for reporting.
    output_dir : str
        Directory to save calibration report CSV.
    """
    log = ["=" * 60]
    log.append("CALIBRATION CURVE FITTING")
    log.append("=" * 60)
    log.append(f"Analyte       : {compound_name}")
    log.append(f"Model         : {model}")
    log.append(f"Weighting     : {weighting}")
    log.append(f"N calibrators : {len(nominal_conc)}")

    os.makedirs(output_dir, exist_ok=True)
    x = np.array(nominal_conc, dtype=float)
    y = np.array(peak_area_ratios, dtype=float)

    # Calculate weights
    if weighting == "1/x":
        w = 1.0 / x
    elif weighting == "1/x2":
        w = 1.0 / (x ** 2)
    elif weighting == "1/y":
        w = 1.0 / y
    elif weighting == "1/y2":
        w = 1.0 / (y ** 2)
    else:
        w = np.ones_like(x)
    w = w / w.sum() * len(w)  # normalise

    try:
        if model == "linear":
            coeffs = np.polyfit(x, y, 1, w=w)
            slope, intercept = coeffs
            y_fit = slope * x + intercept

            def back_calc(y_val):
                return (y_val - intercept) / slope

            log.append(f"\n  Equation  : y = {slope:.6g} × x + {intercept:.6g}")
            log.append(f"  Slope     : {slope:.6g}")
            log.append(f"  Intercept : {intercept:.6g}")

        elif model == "quadratic":
            coeffs = np.polyfit(x, y, 2, w=w)
            a, b, c = coeffs
            y_fit = a * x ** 2 + b * x + c

            def back_calc(y_val):
                disc = b ** 2 - 4 * a * (c - y_val)
                return (-b + np.sqrt(max(disc, 0))) / (2 * a)

            log.append(f"\n  Equation  : y = {a:.4g}×x² + {b:.4g}×x + {c:.4g}")

        # Residuals and R²
        ss_res = np.sum(w * (y - y_fit) ** 2)
        ss_tot = np.sum(w * (y - np.average(y, weights=w)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

        log.append(f"  R²        : {r2:.6f}")
        if r2 < 0.99:
            log.append("  ⚠  R² < 0.99 — review curve fit and outliers.")

        # Back-calculated accuracy
        log.append("\n── Back-Calculated Accuracy ──")
        log.append("  FDA/EMA criterion: ≤15% bias (≤20% at LLOQ)")
        log.append(f"\n  {'Level':<8} {'Nominal':>10} {'Meas. Ratio':>14} {'Back-calc':>12} {'Bias%':>8} {'Pass'}")
        log.append(f"  {'-' * 60}")
        n_pass = 0
        results = []
        for i, (xnom, ymeas) in enumerate(zip(x, y)):
            xbc = back_calc(ymeas)
            bias_pct = (xbc - xnom) / xnom * 100
            is_lloq = lloq_target and abs(xnom - lloq_target) / lloq_target < 0.1
            limit = 20.0 if is_lloq else 15.0
            passed = abs(bias_pct) <= limit
            if passed:
                n_pass += 1
            label = "LLOQ" if is_lloq else f"CS{i+1}"
            pass_str = "✓" if passed else "✗"
            log.append(f"  {label:<8} {xnom:>10.4g} {ymeas:>14.6g} {xbc:>12.4g} {bias_pct:>8.1f} {pass_str}")
            results.append({"level": label, "nominal": xnom, "measured_ratio": ymeas,
                             "back_calc": xbc, "bias_pct": bias_pct, "pass": passed})

        pct_pass = n_pass / len(x) * 100
        log.append(f"\n  Standards passing: {n_pass}/{len(x)}  ({pct_pass:.0f}%)")
        log.append("  FDA: ≥75% of standards must pass (minimum 6 of 8).")
        if pct_pass >= 75 and n_pass >= 6:
            log.append("  ✓ Calibration curve ACCEPTABLE.")
        else:
            log.append("  ✗ Calibration curve FAILS acceptance criteria.")

        pd.DataFrame(results).to_csv(f"{output_dir}/cal_curve_{compound_name}.csv", index=False)
        log.append(f"\nCalibration data saved: {output_dir}/cal_curve_{compound_name}.csv")

    except Exception as exc:
        log.append(f"\nFitting error: {exc}")

    return "\n".join(log)


def assess_method_validation(
    compound_name: str,
    lloq: float,
    uloq: float,
    intraday_data: dict,
    interday_data: dict = None,
    matrix_effect_data: dict = None,
    recovery_data: dict = None,
    stability_data: dict = None,
    species: str = "human",
) -> str:
    """Evaluate bioanalytical method validation against FDA/EMA guidelines.

    Assesses precision (CV%), accuracy (bias%), matrix effects (IS-normalised
    ME%), recovery (extraction efficiency), and stability (bench-top, freeze-
    thaw, long-term).

    Parameters
    ----------
    compound_name : str
        Analyte name.
    lloq : float
        Lower limit of quantification concentration.
    uloq : float
        Upper limit of quantification concentration.
    intraday_data : dict
        {'low': [conc1, conc2, ...], 'mid': [...], 'high': [...]}
        measured concentrations at 3 QC levels (each list = replicates, n≥5).
    interday_data : dict
        Same structure as intraday_data but across ≥3 different days.
    matrix_effect_data : dict
        {'post_extraction_signal': [...], 'neat_standard_signal': [...]}
    recovery_data : dict
        {'extracted': [...], 'unextracted': [...]} at each QC level.
    stability_data : dict
        {'condition': str, 'nominal': float, 'measured': [...]}
    species : str
        Matrix species (human, rat, etc.).
    """
    log = ["=" * 60]
    log.append("BIOANALYTICAL METHOD VALIDATION ASSESSMENT")
    log.append("FDA Bioanalytical Method Validation Guidance (2018)")
    log.append("EMA Guideline on Bioanalytical Method Validation (2011)")
    log.append("=" * 60)
    log.append(f"Analyte : {compound_name}  |  Matrix: {species} plasma")
    log.append(f"LLOQ: {lloq}  |  ULOQ: {uloq}  |  Dynamic range: {uloq/lloq:.0f}×")

    def calc_stats(data):
        arr = np.array(data)
        mean = np.mean(arr)
        sd = np.std(arr, ddof=1)
        cv = sd / mean * 100 if mean != 0 else np.nan
        return mean, sd, cv

    # Precision and accuracy
    for label, data in [("Intraday", intraday_data)] + ([("Interday", interday_data)] if interday_data else []):
        log.append(f"\n── {label} Precision & Accuracy ──")
        log.append("  FDA/EMA: CV ≤15% (≤20% at LLOQ); Bias ≤±15% (±20% at LLOQ)")
        log.append(f"\n  {'QC Level':<12} {'Nominal':>10} {'Mean':>10} {'SD':>8} {'CV%':>8} {'Bias%':>8} {'Prec':>6} {'Acc':>6}")
        log.append(f"  {'-' * 70}")
        nominal_map = {"low": lloq * 3, "mid": (lloq * 3 + uloq * 0.75) / 2, "high": uloq * 0.75}
        for qc_level, replicates in data.items():
            nominal = nominal_map.get(qc_level, lloq * 3)
            is_lloq = qc_level == "lloq"
            lim = 20.0 if is_lloq else 15.0
            mean, sd, cv = calc_stats(replicates)
            bias = (mean - nominal) / nominal * 100
            prec_ok = "✓" if cv <= lim else "✗"
            acc_ok = "✓" if abs(bias) <= lim else "✗"
            log.append(f"  {qc_level.upper():<12} {nominal:>10.4g} {mean:>10.4g} {sd:>8.4g} {cv:>8.1f} {bias:>8.1f} {prec_ok:>6} {acc_ok:>6}")

    # Matrix effects
    if matrix_effect_data:
        log.append("\n── Matrix Effect (IS-Normalised) ──")
        log.append("  FDA: IS-normalised ME should be ≤15% CV across 6 matrix lots.")
        post = np.array(matrix_effect_data.get("post_extraction_signal", []))
        neat = np.array(matrix_effect_data.get("neat_standard_signal", []))
        if len(post) == len(neat) and len(post) > 0:
            me_pct = (post / neat - 1) * 100
            log.append(f"  Mean ME: {np.mean(me_pct):.1f}%  |  CV: {np.std(me_pct,ddof=1)/abs(np.mean(me_pct))*100:.1f}%")
            log.append(f"  {'✓' if np.std(me_pct,ddof=1)/abs(np.mean(me_pct))*100 <= 15 else '✗'} CV of IS-normalised ME")

    # Recovery
    if recovery_data:
        log.append("\n── Extraction Recovery ──")
        log.append("  FDA: Consistent recovery (not necessarily 100%); CV ≤15%.")
        ext = np.array(recovery_data.get("extracted", []))
        unext = np.array(recovery_data.get("unextracted", []))
        if len(ext) == len(unext):
            rec = ext / unext * 100
            log.append(f"  Mean recovery: {np.mean(rec):.1f}%  |  CV: {np.std(rec,ddof=1)/np.mean(rec)*100:.1f}%")

    # Stability
    if stability_data:
        log.append("\n── Stability ──")
        log.append("  FDA/EMA: Bias ≤±15% from nominal after stability condition.")
        for s in stability_data if isinstance(stability_data, list) else [stability_data]:
            meas = np.array(s.get("measured", []))
            nom = s.get("nominal", 1.0)
            bias = (np.mean(meas) - nom) / nom * 100
            cond = s.get("condition", "unknown")
            ok = "✓" if abs(bias) <= 15 else "✗"
            log.append(f"  {ok} {cond:<35} Bias: {bias:+.1f}%")

    log.append("\n── Selectivity & Carry-Over (Manual Assessment Required) ──")
    log.append("  Selectivity: <20% of LLOQ response in 6/6 blank matrices.")
    log.append("  Carry-over: ≤20% of LLOQ signal in blank following ULOQ injection.")
    log.append("  Dilution integrity: QC at 10× ULOQ diluted 10-fold must meet ±15% bias.")

    return "\n".join(log)


def process_lc_ms_concentrations(
    raw_data_path: str,
    calibration_slope: float,
    calibration_intercept: float,
    calibration_model: str = "linear",
    lloq: float = None,
    uloq: float = None,
    dilution_factor: float = 1.0,
    compound_name: str = "analyte",
) -> str:
    """Process raw LC-MS/MS peak area ratios to back-calculated concentrations.

    Reads a CSV with subject/sample peak area ratios, applies the calibration
    curve, flags out-of-range values, applies dilution correction, and
    outputs a cleaned concentration dataset ready for NCA or popPK analysis.

    Parameters
    ----------
    raw_data_path : str
        Path to CSV file. Required columns: sample_id, time, peak_area_ratio.
        Optional: subject_id, evid, dose.
    calibration_slope : float
        Slope from fit_calibration_curve (linear: y = slope×x + intercept).
    calibration_intercept : float
        Intercept from fit_calibration_curve.
    calibration_model : str
        'linear' (only linear supported for back-calculation here).
    lloq : float
        LLOQ — values below this are flagged as BLQ.
    uloq : float
        ULOQ — values above this are flagged as AQL (above quantification limit).
    dilution_factor : float
        Sample dilution factor (multiply back-calculated concentration by this).
    compound_name : str
        Analyte name for output labelling.
    """
    log = ["=" * 60]
    log.append("LC-MS/MS CONCENTRATION PROCESSING")
    log.append("=" * 60)
    log.append(f"Analyte          : {compound_name}")
    log.append(f"Calibration      : y = {calibration_slope:.4g}x + {calibration_intercept:.4g}")
    log.append(f"LLOQ / ULOQ      : {lloq} / {uloq}")
    log.append(f"Dilution factor  : {dilution_factor}×")

    if not os.path.exists(raw_data_path):
        return f"Error: file not found — {raw_data_path}"

    try:
        df = pd.read_csv(raw_data_path)
        log.append(f"\nLoaded {len(df)} rows from {raw_data_path}")

        if "peak_area_ratio" not in df.columns:
            return "Error: 'peak_area_ratio' column not found in data."

        par = df["peak_area_ratio"].values.astype(float)
        conc = (par - calibration_intercept) / calibration_slope * dilution_factor

        df["concentration"] = conc
        df["flag"] = "quantified"
        if lloq:
            df.loc[conc < lloq, "flag"] = "BLQ"
            df.loc[conc < 0, "concentration"] = 0.0
        if uloq:
            df.loc[conc > uloq, "flag"] = "AQL"

        n_blq = (df["flag"] == "BLQ").sum()
        n_aql = (df["flag"] == "AQL").sum()
        n_ok = (df["flag"] == "quantified").sum()

        log.append("\n── Processing Summary ──")
        log.append(f"  Quantified  : {n_ok} ({100*n_ok/len(df):.0f}%)")
        log.append(f"  BLQ         : {n_blq} ({100*n_blq/len(df):.0f}%)  [set to 0 or excluded]")
        log.append(f"  AQL (>ULOQ) : {n_aql} ({100*n_aql/len(df):.0f}%)  [requires re-run at dilution]")

        out_path = raw_data_path.replace(".csv", f"_concentrations_{compound_name}.csv")
        df.to_csv(out_path, index=False)
        log.append(f"\n  Processed dataset saved: {out_path}")
        log.append("  Ready for NCA (run_nca) or popPK input.")

        # BLQ handling guidance
        log.append("\n── BLQ Handling Guidance ──")
        blq_pct = 100 * n_blq / len(df)
        if blq_pct < 5:
            log.append("  BLQ < 5% → exclude BLQ values (M1 method acceptable).")
        elif blq_pct < 20:
            log.append("  BLQ 5–20% → use M2 (first BLQ = LLOQ/2, subsequent = 0).")
        else:
            log.append("  BLQ > 20% → consider censored data methods (M3/M4) in popPK.")
            log.append("  Lloret-Linares method or NONMEM M3 (LAPLACIAN LIKELIHOOD) recommended.")

    except Exception as exc:
        log.append(f"\nProcessing error: {exc}")

    return "\n".join(log)

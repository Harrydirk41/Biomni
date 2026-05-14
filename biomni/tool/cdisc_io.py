"""CDISC data format I/O tools.

Read and write SDTM PC (pharmacokinetic concentration) and PP
(pharmacokinetic parameter) domains, and ADaM ADPC datasets.
Validates structure and reformats for NCA / popPK use.
"""

import os
import re
import numpy as np
import pandas as pd


# SDTM PC required/expected variables
PC_REQUIRED = ["STUDYID", "DOMAIN", "USUBJID", "PCTESTCD", "PCTEST",
               "PCORRES", "PCORRESU", "PCSTRESC", "PCSTRESN", "PCSTRESU",
               "VISITNUM", "PCDTC"]
PC_RECOMMENDED = ["PCSEQ", "PCGRPID", "PCTPT", "PCTPTNUM", "PCELTM",
                  "PCTPTREF", "PCRFTDTC", "PCSPEC", "PCLLOQ", "PCBLFL"]


def read_sdtm_pc(
    pc_file_path: str,
    analyte_filter: str = None,
    specimen_filter: str = "PLASMA",
    exclude_blq: bool = False,
    output_path: str = None,
) -> str:
    """Read and validate a CDISC SDTM PC domain dataset.

    Parses the PC domain, checks required variables, identifies analytes,
    reports data coverage (subjects, time points, BLQ %), and converts to
    analysis-ready long format for NCA or popPK input.

    Parameters
    ----------
    pc_file_path : str
        Path to PC domain CSV or SAS xpt file.
    analyte_filter : str
        PCTESTCD value to filter (e.g. 'DRUG1'). None returns all analytes.
    specimen_filter : str
        PCSPEC value to filter (e.g. 'PLASMA', 'BLOOD', 'URINE').
    exclude_blq : bool
        If True, remove records where PCSTRESC == 'BLQ' or PCSTRESN < PCLLOQ.
    output_path : str
        If provided, save reformatted dataset to this path.
    """
    log = ["=" * 60]
    log.append("SDTM PC DOMAIN READER")
    log.append("=" * 60)

    if not os.path.exists(pc_file_path):
        return f"Error: file not found — {pc_file_path}"

    try:
        if pc_file_path.endswith(".xpt"):
            try:
                import pyreadstat
                df, meta = pyreadstat.read_xport(pc_file_path)
            except ImportError:
                return "Error: pyreadstat required for .xpt files. Install with: pip install pyreadstat"
        else:
            df = pd.read_csv(pc_file_path)

        log.append(f"Loaded: {len(df)} rows × {len(df.columns)} columns")
        log.append(f"File  : {pc_file_path}")

        # Check required variables
        missing = [v for v in PC_REQUIRED if v not in df.columns]
        log.append("\n── SDTM Compliance ──")
        if missing:
            log.append(f"  ⚠ Missing required variables: {', '.join(missing)}")
        else:
            log.append("  ✓ All required PC variables present")

        # Filter
        if specimen_filter and "PCSPEC" in df.columns:
            df = df[df["PCSPEC"].str.upper() == specimen_filter.upper()]
            log.append(f"  Specimen filter: {specimen_filter} → {len(df)} rows")

        if analyte_filter and "PCTESTCD" in df.columns:
            df = df[df["PCTESTCD"].str.upper() == analyte_filter.upper()]
            log.append(f"  Analyte filter: {analyte_filter} → {len(df)} rows")

        # Summary
        if "USUBJID" in df.columns:
            log.append("\n── Data Coverage ──")
            log.append(f"  Subjects : {df['USUBJID'].nunique()}")
        if "PCTESTCD" in df.columns:
            log.append(f"  Analytes : {', '.join(df['PCTESTCD'].unique())}")
        if "PCTPTNUM" in df.columns:
            log.append(f"  Time points : {sorted(df['PCTPTNUM'].dropna().unique())}")
        if "PCSTRESN" in df.columns and "PCLLOQ" in df.columns:
            n_blq = (df["PCSTRESN"] < df["PCLLOQ"]).sum()
            log.append(f"  BLQ values  : {n_blq} ({100*n_blq/len(df):.1f}%)")

        if exclude_blq and "PCSTRESN" in df.columns and "PCLLOQ" in df.columns:
            df = df[df["PCSTRESN"] >= df["PCLLOQ"]]
            log.append(f"  After BLQ exclusion: {len(df)} rows")

        # Reformat for NCA/popPK (standard NONMEM-like columns)
        rename_map = {}
        if "USUBJID" in df.columns:
            rename_map["USUBJID"] = "ID"
        if "PCTPTNUM" in df.columns:
            rename_map["PCTPTNUM"] = "TIME"
        elif "PCELTM" in df.columns:
            # Convert ISO 8601 duration (PT2H) to hours
            df["TIME"] = df["PCELTM"].apply(_iso8601_to_hours)
            log.append("  PCELTM converted to hours via ISO 8601 parsing.")
        if "PCSTRESN" in df.columns:
            rename_map["PCSTRESN"] = "DV"
        if "PCSTRESU" in df.columns:
            rename_map["PCSTRESU"] = "UNIT"

        analysis_df = df.rename(columns=rename_map)

        # Add popPK required columns if absent
        if "EVID" not in analysis_df.columns:
            analysis_df["EVID"] = 0
        if "MDV" not in analysis_df.columns:
            analysis_df["MDV"] = 0
        if "AMT" not in analysis_df.columns:
            analysis_df["AMT"] = 0

        if output_path:
            analysis_df.to_csv(output_path, index=False)
            log.append(f"\nAnalysis dataset saved: {output_path}")
            log.append("  Columns: ID, TIME, DV, EVID, MDV, AMT (NCA/popPK ready)")

    except Exception as exc:
        log.append(f"\nError reading PC domain: {exc}")

    return "\n".join(log)


def read_adam_adpc(
    adpc_file_path: str,
    param_filter: str = None,
    output_path: str = None,
) -> str:
    """Read and validate a CDISC ADaM ADPC (PK Concentrations) dataset.

    ADaM ADPC is the analysis-ready form of PC. Checks for ADaMIG-required
    variables (STUDYID, USUBJID, PARAM, AVAL, ADY, ATPT, etc.) and
    generates summary statistics.

    Parameters
    ----------
    adpc_file_path : str
        Path to ADPC dataset CSV or .xpt file.
    param_filter : str
        PARAM value to subset (e.g. 'Drug A in Plasma').
    output_path : str
        Path to save cleaned ADPC subset.
    """
    log = ["=" * 60]
    log.append("ADaM ADPC DATASET READER")
    log.append("=" * 60)

    if not os.path.exists(adpc_file_path):
        return f"Error: file not found — {adpc_file_path}"

    ADPC_REQUIRED = ["STUDYID", "USUBJID", "PARAM", "PARAMCD", "AVAL", "ADY", "ATPT", "DTYPE"]

    try:
        if adpc_file_path.endswith(".xpt"):
            try:
                import pyreadstat
                df, _ = pyreadstat.read_xport(adpc_file_path)
            except ImportError:
                return "Error: pyreadstat required for .xpt files."
        else:
            df = pd.read_csv(adpc_file_path)

        log.append(f"Loaded: {len(df)} rows × {len(df.columns)} columns")

        missing = [v for v in ADPC_REQUIRED if v not in df.columns]
        if missing:
            log.append(f"⚠ Missing ADaM ADPC variables: {', '.join(missing)}")
        else:
            log.append("✓ All key ADaM ADPC variables present")

        if param_filter and "PARAM" in df.columns:
            df = df[df["PARAM"].str.contains(param_filter, case=False, na=False)]
            log.append(f"PARAM filter '{param_filter}': {len(df)} rows remaining")

        log.append("\n── Summary ──")
        if "USUBJID" in df.columns:
            log.append(f"  Subjects : {df['USUBJID'].nunique()}")
        if "PARAM" in df.columns:
            log.append(f"  Parameters : {', '.join(df['PARAM'].unique()[:5])}")
        if "AVAL" in df.columns:
            log.append(f"  AVAL range : {df['AVAL'].min():.3g} – {df['AVAL'].max():.3g}")
        if "DTYPE" in df.columns:
            log.append(f"  DTYPE values : {df['DTYPE'].value_counts().to_dict()}")

        if output_path:
            df.to_csv(output_path, index=False)
            log.append(f"\nSubset saved: {output_path}")

    except Exception as exc:
        log.append(f"\nError: {exc}")

    return "\n".join(log)


def format_nca_results_as_pp_domain(
    nca_results_path: str,
    study_id: str = "STUDY001",
    output_path: str = None,
) -> str:
    """Convert NCA parameter results to CDISC SDTM PP domain format.

    Maps standard NCA output parameters (AUClast, AUCinf, Cmax, Tmax, t½,
    CL/F, Vd/F) to PP domain variable names (PPTESTCD, PPTEST, PPORRES,
    PPSTRESN, PPSTRESU) per CDISC Pharmacokinetics Analysis Data Model.

    Parameters
    ----------
    nca_results_path : str
        Path to NCA results CSV (output from run_nca or PKNCA).
    study_id : str
        STUDYID for PP domain.
    output_path : str
        Path to save the PP domain CSV. Auto-generated if None.
    """
    log = ["=" * 60]
    log.append("NCA → SDTM PP DOMAIN FORMATTER")
    log.append("=" * 60)

    # PKNCA → SDTM PP mapping
    param_map = {
        "auclast":    ("AUCLST",  "AUC From Time Zero to Last Measurable Concentration", "ng*h/mL"),
        "aucinf.obs": ("AUCIFO",  "AUC From Time Zero to Infinity (Observed)",           "ng*h/mL"),
        "cmax":       ("CMAX",    "Maximum Observed Concentration",                       "ng/mL"),
        "tmax":       ("TMAX",    "Time of Maximum Concentration",                        "h"),
        "half.life":  ("LAMZHL",  "Half-Life of Terminal Phase",                          "h"),
        "cl.obs":     ("CLO",     "Apparent Clearance (Observed AUC)",                    "L/h"),
        "cl.last":    ("CLL",     "Apparent Clearance (Last AUC)",                        "L/h"),
        "vd.obs":     ("VZFO",    "Apparent Volume of Distribution (Observed)",           "L"),
        "mrt.obs":    ("MRTO",    "Mean Residence Time (Observed)",                       "h"),
        "lambda.z":   ("LAMZ",    "Terminal Phase Rate Constant",                         "1/h"),
        "r.squared":  ("LAMZR2",  "R-Squared for Terminal Phase",                         ""),
    }

    if not os.path.exists(nca_results_path):
        return f"Error: NCA results file not found — {nca_results_path}"

    try:
        nca_df = pd.read_csv(nca_results_path)
        log.append(f"Loaded NCA results: {nca_results_path}")

        pp_records = []
        pp_seq = 1
        for _, row in nca_df.iterrows():
            testcd_raw = str(row.get("PPTESTCD", row.get("PPTESTCD", ""))).lower()
            value = row.get("PPORRES", row.get("PPSTRESN", np.nan))
            subject = row.get("USUBJID", row.get("subject", "SUBJ001"))

            mapped = param_map.get(testcd_raw)
            if mapped:
                pptestcd, pptest, ppstresu = mapped
                try:
                    val_num = float(value)
                except (ValueError, TypeError):
                    val_num = np.nan
                pp_records.append({
                    "STUDYID":  study_id,
                    "DOMAIN":   "PP",
                    "USUBJID":  subject,
                    "PPSEQ":    pp_seq,
                    "PPTESTCD": pptestcd,
                    "PPTEST":   pptest,
                    "PPORRES":  value,
                    "PPSTRESN": val_num,
                    "PPSTRESU": ppstresu,
                })
                pp_seq += 1

        pp_df = pd.DataFrame(pp_records)
        log.append("\n── PP Domain Generated ──")
        log.append(f"  Records   : {len(pp_df)}")
        log.append(f"  Subjects  : {pp_df['USUBJID'].nunique()}")
        log.append(f"  Parameters: {', '.join(pp_df['PPTESTCD'].unique())}")

        if not output_path:
            output_path = nca_results_path.replace(".csv", "_pp_domain.csv")
        pp_df.to_csv(output_path, index=False)
        log.append(f"\nPP domain saved: {output_path}")

    except Exception as exc:
        log.append(f"\nFormatting error: {exc}")

    return "\n".join(log)


def validate_pk_dataset_for_nonmem(
    dataset_path: str,
    id_col: str = "ID",
    time_col: str = "TIME",
    dv_col: str = "DV",
    amt_col: str = "AMT",
    evid_col: str = "EVID",
) -> str:
    """Validate a PK dataset for NONMEM/nlmixr2 compatibility.

    Checks for common data issues: missing required columns, negative times,
    dose records without AMT, observation records with non-zero AMT,
    duplicate time points per subject, implausible values, and structural
    issues that will cause NONMEM errors.

    Parameters
    ----------
    dataset_path : str
        Path to dataset CSV.
    id_col, time_col, dv_col, amt_col, evid_col : str
        Column names for ID, TIME, DV, AMT, EVID in the dataset.
    """
    log = ["=" * 60]
    log.append("PK DATASET VALIDATION FOR NONMEM/nlmixr2")
    log.append("=" * 60)

    if not os.path.exists(dataset_path):
        return f"Error: file not found — {dataset_path}"

    try:
        df = pd.read_csv(dataset_path, na_values=[".", "NA", ""])
        log.append(f"Dataset: {dataset_path}")
        log.append(f"Shape  : {df.shape[0]} rows × {df.shape[1]} columns")
        log.append(f"Columns: {', '.join(df.columns)}")

        errors = []
        warnings = []

        # Required columns
        for col in [id_col, time_col, dv_col, amt_col, evid_col]:
            if col not in df.columns:
                errors.append(f"Missing required column: {col}")

        if errors:
            log.append("\n── ERRORS (must fix) ──")
            for e in errors:
                log.append(f"  ✗ {e}")
            return "\n".join(log)

        # Subject summary
        n_subj = df[id_col].nunique()
        log.append("\n── Dataset Summary ──")
        log.append(f"  Subjects        : {n_subj}")
        log.append(f"  Dose records    : {(df[evid_col] == 1).sum()}")
        log.append(f"  Obs records     : {(df[evid_col] == 0).sum()}")

        # Check issues
        if (df[time_col] < 0).any():
            n = (df[time_col] < 0).sum()
            warnings.append(f"{n} records with TIME < 0 (pre-dose samples — add EVID=2 or flag)")

        obs = df[df[evid_col] == 0]
        if obs[dv_col].isna().any():
            n = obs[dv_col].isna().sum()
            warnings.append(f"{n} observation records with missing DV (set MDV=1 or remove)")

        dose = df[df[evid_col] == 1]
        if (dose[amt_col].isna() | (dose[amt_col] <= 0)).any():
            n = (dose[amt_col].isna() | (dose[amt_col] <= 0)).sum()
            errors.append(f"{n} dose records with AMT missing or ≤0")

        if (obs[amt_col] > 0).any():
            n = (obs[amt_col] > 0).sum()
            warnings.append(f"{n} observation records (EVID=0) have AMT > 0 (should be 0 or missing)")

        for subj, grp in df.groupby(id_col):
            obs_grp = grp[grp[evid_col] == 0]
            dups = obs_grp[obs_grp.duplicated(subset=[time_col], keep=False)]
            if len(dups) > 0:
                warnings.append(f"Subject {subj}: duplicate TIME in observations at t={dups[time_col].tolist()}")
                break

        # MDV check
        if "MDV" not in df.columns:
            warnings.append("MDV column missing — add MDV=1 for dose records, MDV=0 for observations")

        # LLOQ / BLQ
        if (df[dv_col] == 0).any() and (df[evid_col] == 0).any():
            n_zero = ((df[evid_col] == 0) & (df[dv_col] == 0)).sum()
            warnings.append(f"{n_zero} obs with DV=0 — confirm these are BLQ (handle with M1-M3 method)")

        log.append("\n── Validation Results ──")
        if errors:
            log.append("  ERRORS (must fix before running):")
            for e in errors:
                log.append(f"    ✗ {e}")
        else:
            log.append("  ✓ No blocking errors found.")

        if warnings:
            log.append(f"\n  WARNINGS ({len(warnings)}):")
            for w in warnings[:10]:
                log.append(f"    ⚠  {w}")
        else:
            log.append("  ✓ No warnings.")

        log.append("\n── Data Quality Metrics ──")
        log.append(f"  DV range (obs): {obs[dv_col].min():.3g} – {obs[dv_col].max():.3g}")
        log.append(f"  TIME range    : {df[time_col].min():.1f} – {df[time_col].max():.1f} h")
        log.append(f"  Obs/subject   : {len(obs)/n_subj:.1f} (mean)")

    except Exception as exc:
        log.append(f"\nValidation error: {exc}")

    return "\n".join(log)


def _iso8601_to_hours(duration_str: str) -> float:
    """Convert ISO 8601 duration string (e.g. PT2H30M) to hours."""
    if pd.isna(duration_str) or not isinstance(duration_str, str):
        return np.nan
    m = re.match(r"PT?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", str(duration_str).upper())
    if not m:
        return np.nan
    h = float(m.group(1) or 0)
    mins = float(m.group(2) or 0)
    secs = float(m.group(3) or 0)
    return h + mins / 60 + secs / 3600

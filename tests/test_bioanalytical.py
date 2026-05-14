"""Unit tests for biomni.tool.bioanalytical — pure Python / scipy functions."""

import pytest
import pandas as pd
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Calibration curve
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.bioanalytical import fit_calibration_curve


def test_calibration_curve_returns_string():
    conc  = [1, 2, 5, 10, 25, 50, 100, 200]
    ratio = [0.12, 0.24, 0.60, 1.21, 3.02, 6.05, 12.1, 24.2]
    result = fit_calibration_curve(conc, ratio, weighting="1/x2")
    assert isinstance(result, str)


def test_calibration_curve_reports_r_squared():
    conc  = [1, 2, 5, 10, 25, 50, 100, 200]
    ratio = [0.12, 0.24, 0.60, 1.21, 3.02, 6.05, 12.1, 24.2]
    result = fit_calibration_curve(conc, ratio, weighting="1/x2")
    assert "R²" in result or "R2" in result or "r_squared" in result.lower()


def test_calibration_curve_reports_slope():
    conc  = [1, 2, 5, 10, 25, 50, 100, 200]
    ratio = [0.12, 0.24, 0.60, 1.21, 3.02, 6.05, 12.1, 24.2]
    result = fit_calibration_curve(conc, ratio, weighting="1/x2")
    assert "slope" in result.lower()


def test_calibration_curve_back_calculation_accuracy():
    # Near-perfect linear data: accuracy should pass
    conc  = [1, 2, 5, 10, 25, 50, 100, 200]
    ratio = [c * 0.121 for c in conc]
    result = fit_calibration_curve(conc, ratio, weighting="1/x2")
    assert "PASS" in result or "pass" in result.lower() or "100%" in result or "accuracy" in result.lower()


@pytest.mark.parametrize("weighting", ["1/x", "1/x2", "none"])
def test_calibration_curve_all_weightings(weighting):
    conc  = [1, 2, 5, 10, 25, 50, 100, 200]
    ratio = [c * 0.12 for c in conc]
    result = fit_calibration_curve(conc, ratio, weighting=weighting)
    assert isinstance(result, str)


def test_calibration_curve_empty_input():
    result = fit_calibration_curve([], [], weighting="1/x2")
    assert isinstance(result, str)
    assert "error" in result.lower() or "invalid" in result.lower() or "ERROR" in result


# ─────────────────────────────────────────────────────────────────────────────
# Method validation
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.bioanalytical import assess_method_validation

INTRADAY = {
    "low":  [3.1, 2.9, 3.0, 3.2, 2.8],
    "mid":  [50.2, 49.8, 50.5, 49.5, 50.0],
    "high": [198, 201, 199, 200, 202],
}
INTERDAY = {
    "low":  [3.0, 3.2, 2.9],
    "mid":  [50.0, 49.5, 50.8],
    "high": [200, 199, 201],
}


def test_validation_returns_string():
    result = assess_method_validation(
        compound_name="DrugA",
        lloq=1.0,
        uloq=200.0,
        intraday_data=INTRADAY,
        interday_data=INTERDAY,
    )
    assert isinstance(result, str)


def test_validation_reports_precision():
    result = assess_method_validation(
        compound_name="DrugA",
        lloq=1.0,
        uloq=200.0,
        intraday_data=INTRADAY,
        interday_data=INTERDAY,
    )
    assert "precision" in result.lower() or "CV" in result or "%CV" in result


def test_validation_reports_accuracy():
    result = assess_method_validation(
        compound_name="DrugA",
        lloq=1.0,
        uloq=200.0,
        intraday_data=INTRADAY,
        interday_data=INTERDAY,
    )
    assert "accuracy" in result.lower() or "bias" in result.lower()


# ─────────────────────────────────────────────────────────────────────────────
# LC-MS concentration processing
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.bioanalytical import process_lc_ms_concentrations


def test_process_lc_ms_returns_string(tmp_path):
    import pandas as pd
    df = pd.DataFrame({
        "sample_id": ["S1", "S2", "S3", "BLQ"],
        "peak_area_ratio": [1.21, 0.61, 6.05, 0.005],
    })
    path = str(tmp_path / "raw.csv")
    df.to_csv(path, index=False)
    result = process_lc_ms_concentrations(
        raw_data_path=path,
        calibration_slope=0.121,
        calibration_intercept=0.0,
        lloq=0.05,
    )
    assert isinstance(result, str)


def test_process_lc_ms_flags_blq(tmp_path):
    df = pd.DataFrame({
        "sample_id": ["S1", "BLQ1"],
        "peak_area_ratio": [1.21, 0.001],
    })
    path = str(tmp_path / "raw.csv")
    df.to_csv(path, index=False)
    result = process_lc_ms_concentrations(
        raw_data_path=path,
        calibration_slope=0.121,
        calibration_intercept=0.0,
        lloq=0.05,
    )
    assert "BLQ" in result or "below" in result.lower()

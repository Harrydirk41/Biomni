"""Unit tests for biomni.tool.poppk — pure Python functions only.

R-dependent functions (run_nlmixr2_model, run_mrgsolve_simulation,
run_covariate_analysis) are tested via mock.
"""

import pytest
import tempfile
import os
from unittest.mock import patch


# ─────────────────────────────────────────────────────────────────────────────
# NONMEM control stream generation (pure Python)
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.poppk import generate_nonmem_control_stream


def test_generates_1cmt_iv_stream(tmp_path):
    result = generate_nonmem_control_stream(
        model_type="1cmt_iv",
        dataset_path="/data/pk.csv",
        output_path=str(tmp_path / "run001"),
    )
    assert isinstance(result, str)
    assert "$PROBLEM" in result or "PROBLEM" in result or "1cmt" in result.lower()


def test_generates_2cmt_oral_stream(tmp_path):
    result = generate_nonmem_control_stream(
        model_type="2cmt_oral",
        dataset_path="/data/pk.csv",
        output_path=str(tmp_path / "run002"),
    )
    assert isinstance(result, str)


@pytest.mark.parametrize("model_type", [
    "1cmt_iv", "2cmt_iv", "1cmt_oral", "2cmt_oral", "emax"
])
def test_all_model_types_return_string(tmp_path, model_type):
    result = generate_nonmem_control_stream(
        model_type=model_type,
        dataset_path="/data/pk.csv",
        output_path=str(tmp_path / f"run_{model_type}"),
    )
    assert isinstance(result, str), f"{model_type} did not return str"


# ─────────────────────────────────────────────────────────────────────────────
# NONMEM output parsing (pure Python — regex)
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.poppk import parse_nonmem_output

MINIMAL_LST = """
 NONLINEAR MIXED EFFECTS MODEL PROGRAM (NONMEM)
 #OBJV:  *******   1234.567

 THETA - VECTOR OF FIXED EFFECTS PARAMETERS   *********
 TH 1 = 5.12000E+00  SE = 3.25000E-01
 TH 2 = 4.85000E+01  SE = 8.40000E+00

 OMEGA - COV MATRIX FOR RANDOM EFFECTS - ETAS  ********
 ETA1  +1.23456E-01

 SIGMA - COV MATRIX FOR RANDOM EFFECTS - EPS  *********
 EPS1  +4.56789E-02

 MINIMIZATION SUCCESSFUL

 COVARIANCE STEP ABORTED
"""


def test_parse_ofv(tmp_path):
    lst_file = tmp_path / "run001.lst"
    lst_file.write_text(MINIMAL_LST)
    result = parse_nonmem_output(str(lst_file))
    assert isinstance(result, str)
    assert "1234" in result or "OFV" in result


def test_parse_theta_values(tmp_path):
    lst_file = tmp_path / "run001.lst"
    lst_file.write_text(MINIMAL_LST)
    result = parse_nonmem_output(str(lst_file))
    assert "THETA" in result or "5.12" in result


def test_parse_covariance_aborted_flag(tmp_path):
    lst_file = tmp_path / "run001.lst"
    lst_file.write_text(MINIMAL_LST)
    result = parse_nonmem_output(str(lst_file))
    assert "ABORTED" in result or "aborted" in result.lower() or "covariance" in result.lower()


def test_parse_missing_file():
    result = parse_nonmem_output("/nonexistent/path/run.lst")
    assert isinstance(result, str)
    assert "error" in result.lower() or "not found" in result.lower() or "ERROR" in result


# ─────────────────────────────────────────────────────────────────────────────
# Model comparison (pure Python)
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.poppk import compare_pk_models


MODEL_RESULTS = [
    {"name": "1cmt_oral", "ofv": 1200.5, "n_params": 4,
     "n_obs": 800, "cov_successful": True, "vpc_pass": True},
    {"name": "2cmt_oral", "ofv": 1185.2, "n_params": 6,
     "n_obs": 800, "cov_successful": True, "vpc_pass": True},
    {"name": "3cmt_oral", "ofv": 1183.0, "n_params": 9,
     "n_obs": 800, "cov_successful": False, "vpc_pass": False},
]


def test_compare_returns_string():
    result = compare_pk_models(MODEL_RESULTS)
    assert isinstance(result, str)


def test_compare_includes_aic():
    result = compare_pk_models(MODEL_RESULTS)
    assert "AIC" in result


def test_compare_includes_lrt():
    result = compare_pk_models(MODEL_RESULTS)
    assert "LRT" in result or "likelihood" in result.lower() or "OFV" in result


def test_compare_flags_failed_cov():
    result = compare_pk_models(MODEL_RESULTS)
    # 3cmt has cov_successful=False — should be flagged
    assert "✗" in result or "ABORTED" in result or "failed" in result.lower()


def test_compare_recommends_model():
    result = compare_pk_models(MODEL_RESULTS)
    assert "recommend" in result.lower() or "best" in result.lower() or "selected" in result.lower()


def test_compare_empty_input():
    result = compare_pk_models([])
    assert isinstance(result, str)


def test_compare_single_model():
    result = compare_pk_models([MODEL_RESULTS[0]])
    assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# R-dependent functions — tested via mock
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.poppk import run_nlmixr2_model, run_mrgsolve_simulation


def test_nlmixr2_calls_r(tmp_path):
    import pandas as pd
    df = pd.DataFrame({
        "ID": [1, 1, 1], "TIME": [0, 1, 4],
        "DV": [0, 100.5, 45.2], "AMT": [100, 0, 0],
        "EVID": [1, 0, 0], "MDV": [1, 0, 0],
    })
    csv_path = str(tmp_path / "pk.csv")
    df.to_csv(csv_path, index=False)

    with patch("biomni.tool.poppk.run_r_code") as mock_r:
        mock_r.return_value = "nlmixr2 fit complete\nOFV: 1234.5\nCL: 5.2\nVd: 48.3"
        result = run_nlmixr2_model(
            dataset_path=csv_path,
            model_type="1cmt_oral",
            estimation_method="SAEM",
            output_dir=str(tmp_path),
        )
        assert mock_r.called
        assert isinstance(result, str)


def test_mrgsolve_calls_r():
    with patch("biomni.tool.poppk.run_r_code") as mock_r:
        mock_r.return_value = "Simulation complete\nCmax: 125.3\nAUC: 890.2\nTmax: 2.0"
        result = run_mrgsolve_simulation(
            model_type="1cmt_oral",
            pk_parameters={"CL": 5.0, "Vd": 50.0, "KA": 1.2},
            dosing_regimen={"dose": 100, "interval": 24, "n_doses": 7},
            n_subjects=100,
        )
        assert mock_r.called
        assert isinstance(result, str)

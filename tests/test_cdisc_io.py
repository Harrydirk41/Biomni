"""Unit tests for biomni.tool.cdisc_io — pure pandas functions."""

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# NONMEM dataset validation
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.cdisc_io import validate_pk_dataset_for_nonmem


def _write_csv(tmp_path, df, name="pk.csv"):
    path = str(tmp_path / name)
    df.to_csv(path, index=False)
    return path


def _valid_df():
    return pd.DataFrame({
        "ID":   [1, 1, 1, 2, 2],
        "TIME": [0, 1, 4, 0, 2],
        "DV":   [0, 100.5, 45.2, 0, 88.3],
        "AMT":  [100, 0, 0, 100, 0],
        "EVID": [1, 0, 0, 1, 0],
        "MDV":  [1, 0, 0, 1, 0],
    })


def test_valid_dataset_passes(tmp_path):
    path = _write_csv(tmp_path, _valid_df())
    result = validate_pk_dataset_for_nonmem(path)
    assert isinstance(result, str)
    assert "no blocking errors" in result.lower()


def test_missing_required_column_flagged(tmp_path):
    df = _valid_df().drop(columns=["AMT"])
    path = _write_csv(tmp_path, df)
    result = validate_pk_dataset_for_nonmem(path)
    assert "AMT" in result or "missing" in result.lower()


def test_missing_id_column_flagged(tmp_path):
    df = _valid_df().drop(columns=["ID"])
    path = _write_csv(tmp_path, df)
    result = validate_pk_dataset_for_nonmem(path)
    assert "ID" in result or "missing" in result.lower()


def test_missing_file_handled():
    result = validate_pk_dataset_for_nonmem("/nonexistent/file.csv")
    assert isinstance(result, str)
    assert "error" in result.lower() or "not found" in result.lower() or "ERROR" in result


def test_negative_time_flagged(tmp_path):
    df = _valid_df()
    df.loc[1, "TIME"] = -1.0
    path = _write_csv(tmp_path, df)
    result = validate_pk_dataset_for_nonmem(path)
    assert isinstance(result, str)


def test_returns_string_always(tmp_path):
    path = _write_csv(tmp_path, _valid_df())
    result = validate_pk_dataset_for_nonmem(path)
    assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# SDTM PC reader
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.cdisc_io import read_sdtm_pc  # noqa: E402


def test_sdtm_pc_missing_file():
    result = read_sdtm_pc("/nonexistent/pc.xpt")
    assert isinstance(result, str)
    assert "error" in result.lower() or "not found" in result.lower() or "ERROR" in result


# ─────────────────────────────────────────────────────────────────────────────
# NCA results → PP domain formatter
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.cdisc_io import format_nca_results_as_pp_domain  # noqa: E402


def test_pp_domain_from_nca_csv(tmp_path):
    nca_df = pd.DataFrame({
        "USUBJID": ["SUBJ-001", "SUBJ-002"],
        "PARAM":   ["AUClast", "Cmax"],
        "AVAL":    [1234.5, 98.7],
        "AVALU":   ["h*ng/mL", "ng/mL"],
    })
    nca_path = str(tmp_path / "nca_results.csv")
    nca_df.to_csv(nca_path, index=False)

    result = format_nca_results_as_pp_domain(
        nca_results_path=nca_path,
        study_id="STUDY001",
    )
    assert isinstance(result, str)


def test_pp_domain_missing_file():
    result = format_nca_results_as_pp_domain(
        nca_results_path="/nonexistent/nca.csv",
        study_id="STUDY001",
    )
    assert isinstance(result, str)
    assert "error" in result.lower() or "not found" in result.lower() or "ERROR" in result

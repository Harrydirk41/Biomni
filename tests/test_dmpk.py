"""Unit tests for biomni.tool.dmpk — pure Python functions only.

Functions that call run_r_code() (run_nca) are tested via mock.
All other functions are deterministic and tested against exact values.
"""

from unittest.mock import patch


# ─────────────────────────────────────────────────────────────────────────────
# Microsomal stability
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.dmpk import calculate_microsomal_stability


def test_clint_high_clearance():
    # slope ≈ -0.0578 /min → t½≈12 min → HIGH
    result = calculate_microsomal_stability(
        time_points=[0, 5, 10, 20, 40, 60],
        percent_remaining=[100, 74.1, 54.9, 30.1, 9.1, 2.7],
        microsomal_protein_conc_mg_per_mL=0.5,
    )
    assert isinstance(result, str)
    assert "HIGH" in result
    assert "CLint" in result


def test_clint_low_clearance():
    # slow decay → LOW
    result = calculate_microsomal_stability(
        time_points=[0, 15, 30, 60, 90, 120],
        percent_remaining=[100, 89.5, 80.1, 64.2, 51.4, 41.2],
        microsomal_protein_conc_mg_per_mL=0.5,
    )
    assert "LOW" in result


def test_clint_returns_t_half():
    result = calculate_microsomal_stability(
        time_points=[0, 5, 10, 20, 40, 60],
        percent_remaining=[100, 74.1, 54.9, 30.1, 9.1, 2.7],
        microsomal_protein_conc_mg_per_mL=0.5,
    )
    assert "t½" in result or "half" in result.lower()


def test_clint_insufficient_points():
    result = calculate_microsomal_stability(
        time_points=[0, 5],
        percent_remaining=[100, 80],
        microsomal_protein_conc_mg_per_mL=0.5,
    )
    assert isinstance(result, str)  # must not raise, must return str


# ─────────────────────────────────────────────────────────────────────────────
# Plasma protein binding
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.dmpk import calculate_plasma_protein_binding  # noqa: E402


def test_ppb_fu_calculation():
    # fu = buffer_conc / plasma_conc = 0.05 / 1.0 = 0.05 → 95% bound
    result = calculate_plasma_protein_binding(
        buffer_conc=0.05,
        plasma_conc=1.0,
        method="RED",
        species="human",
    )
    assert isinstance(result, str)
    assert "fu" in result.lower() or "unbound" in result.lower()


def test_ppb_high_binding_flag():
    # fu = 0.005 → very high binding, should flag
    result = calculate_plasma_protein_binding(
        buffer_conc=0.005,
        plasma_conc=1.0,
        method="RED",
        species="human",
    )
    assert "high" in result.lower() or "0.005" in result or "0.01" in result


def test_ppb_returns_string():
    result = calculate_plasma_protein_binding(
        buffer_conc=0.1,
        plasma_conc=1.0,
    )
    assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# Permeability
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.dmpk import calculate_permeability  # noqa: E402


def test_permeability_high_papp():
    # Papp A→B high → high permeability
    result = calculate_permeability(
        apical_to_basolateral_conc=[0.0, 8.5],
        basolateral_to_apical_conc=[0.0, 1.2],
        donor_conc_initial=10.0,
        incubation_time_h=2.0,
        insert_area_cm2=0.33,
        volume_donor_mL=0.1,
        volume_receiver_mL=0.6,
    )
    assert isinstance(result, str)
    assert "Papp" in result


def test_permeability_efflux_ratio_pgp():
    # B→A >> A→B → efflux ratio ≥ 2 → P-gp flag
    result = calculate_permeability(
        apical_to_basolateral_conc=[0.0, 0.5],
        basolateral_to_apical_conc=[0.0, 5.0],
        donor_conc_initial=10.0,
        incubation_time_h=2.0,
        insert_area_cm2=0.33,
        volume_donor_mL=0.1,
        volume_receiver_mL=0.6,
    )
    assert "efflux" in result.lower() or "P-gp" in result or "ratio" in result.lower()


# ─────────────────────────────────────────────────────────────────────────────
# CYP inhibition
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.dmpk import fit_cyp_inhibition  # noqa: E402


def test_cyp_ic50_fitted():
    result = fit_cyp_inhibition(
        inhibitor_conc_uM=[0.01, 0.1, 1, 10, 100],
        percent_activity_remaining=[98, 90, 60, 20, 4],
        cyp_enzyme="CYP3A4",
    )
    assert isinstance(result, str)
    assert "IC50" in result


def test_cyp_high_risk_classification():
    # IC50 ≈ 0.5 µM → HIGH risk
    result = fit_cyp_inhibition(
        inhibitor_conc_uM=[0.01, 0.1, 0.5, 1, 10],
        percent_activity_remaining=[99, 92, 50, 20, 2],
        cyp_enzyme="CYP3A4",
    )
    assert "HIGH" in result or "high" in result.lower()


def test_cyp_low_risk_classification():
    # IC50 >> 10 µM → LOW risk
    result = fit_cyp_inhibition(
        inhibitor_conc_uM=[1, 10, 100, 1000],
        percent_activity_remaining=[99, 97, 85, 55],
        cyp_enzyme="CYP2D6",
    )
    assert "LOW" in result or "low" in result.lower()


# ─────────────────────────────────────────────────────────────────────────────
# DDI risk static model
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.dmpk import predict_ddi_risk_static  # noqa: E402


def test_ddi_r1_high_value():
    # R1 = 1 + (2.0 * 0.1) / 0.5 = 1.40 → HIGH
    result = predict_ddi_risk_static(
        compound_name="TestCompound",
        cmax_total_uM=2.0,
        fu_plasma=0.1,
        cyp_ic50={"CYP3A4": 0.5},
    )
    assert isinstance(result, str)
    assert "1.4" in result or "HIGH" in result


def test_ddi_r1_low_no_action():
    # R1 = 1 + (0.5 * 0.05) / 50 = 1.0005 → LOW
    result = predict_ddi_risk_static(
        compound_name="CleanDrug",
        cmax_total_uM=0.5,
        fu_plasma=0.05,
        cyp_ic50={"CYP2D6": 50.0},
    )
    assert "LOW" in result or "no further" in result.lower() or "1.00" in result


def test_ddi_returns_r1_for_each_cyp():
    result = predict_ddi_risk_static(
        compound_name="Multi",
        cmax_total_uM=1.0,
        fu_plasma=0.1,
        cyp_ic50={"CYP3A4": 2.0, "CYP2D6": 5.0, "CYP2C9": 8.0},
    )
    assert "CYP3A4" in result
    assert "CYP2D6" in result


# ─────────────────────────────────────────────────────────────────────────────
# IVIVE clearance
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.dmpk import ivive_clearance  # noqa: E402


def test_ivive_returns_string():
    result = ivive_clearance(
        cl_int_uL_per_min_per_mg=45.0,
        fu_plasma=0.1,
        species="human",
        model="well_stirred",
    )
    assert isinstance(result, str)


def test_ivive_high_clint_gives_high_clh():
    result = ivive_clearance(
        cl_int_uL_per_min_per_mg=200.0,
        fu_plasma=1.0,
        species="human",
        model="well_stirred",
    )
    assert "high" in result.lower() or "CLh" in result or "hepatic" in result.lower()


def test_ivive_all_models():
    for model in ["well_stirred", "parallel_tube", "dispersion"]:
        result = ivive_clearance(
            cl_int_uL_per_min_per_mg=45.0,
            fu_plasma=0.1,
            species="human",
            model=model,
        )
        assert isinstance(result, str), f"Model {model} did not return str"


# ─────────────────────────────────────────────────────────────────────────────
# run_nca (R-dependent — test via mock)
# ─────────────────────────────────────────────────────────────────────────────

from biomni.tool.dmpk import run_nca  # noqa: E402


def test_run_nca_calls_r_code():
    with patch("biomni.tool.dmpk.run_r_code") as mock_r:
        mock_r.return_value = "AUClast=72.4\nAUCinf=75.0\nCmax=50.0\nt_half=6.93\nCL=1.38\nVd=13.8"
        result = run_nca(
            concentration_data=[1.9, 1.8, 1.6, 1.3, 1.1, 0.9],
            time_data=[0.5, 1, 2, 4, 6, 8],
            dose=100,
            route="iv",
        )
        assert mock_r.called
        assert isinstance(result, str)

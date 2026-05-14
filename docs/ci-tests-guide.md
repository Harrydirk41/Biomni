# CI Testing Guide — Biomni PKPD Agent

## What Exists Now

```
.github/workflows/ci-cd.yml   ← full pipeline (lint + test + build + deploy + benchmark)
tests/
  __init__.py
  test_dmpk.py                ← 22 tests: CLint, PPB, Caco-2, CYP, DDI R1, IVIVE, NCA mock
  test_poppk.py               ← 17 tests: NONMEM stream gen, output parsing, model comparison
  test_bioanalytical.py       ← 13 tests: calibration curve, method validation, LC-MS
  test_cdisc_io.py            ← 10 tests: NONMEM dataset validation, SDTM PC, PP domain
  test_tool_contracts.py      ← 5 parametrised tests: return type, docstring, type hints
  test_pkpd_agent.py          ← 16 tests: LangSmith helper, system context, know-how files
```

Total: ~83 tests. All run in <60 seconds with no LLM API calls, no R, no AWS.

---

## Running Tests Locally

```bash
# Install test dependencies
pip install pytest pytest-cov numpy pandas scipy langchain-core langgraph

# Install biomni in editable mode
pip install -e . --no-deps

# Run all tests
pytest tests/ -v

# Run a specific file
pytest tests/test_dmpk.py -v

# Run a specific test
pytest tests/test_dmpk.py::test_ddi_r1_high_value -v

# Run with coverage report
pytest tests/ --cov=biomni/tool --cov=biomni/agent/pkpd_agent.py --cov-report=term-missing
```

---

## What Each Test File Covers

### `test_dmpk.py` — DMPK Tool Functions

| Test | What it checks |
|---|---|
| `test_clint_high_clearance` | t½ < 30 min → "HIGH" in output |
| `test_clint_low_clearance` | t½ > 60 min → "LOW" in output |
| `test_clint_insufficient_points` | 2-point data → returns str, doesn't crash |
| `test_ppb_fu_calculation` | fu reported in output |
| `test_ppb_high_binding_flag` | fu < 0.01 → flagged |
| `test_permeability_efflux_ratio_pgp` | B→A >> A→B → efflux/P-gp mentioned |
| `test_cyp_ic50_fitted` | "IC50" appears in output |
| `test_cyp_high_risk_classification` | IC50 < 1 µM → "HIGH" |
| `test_cyp_low_risk_classification` | IC50 >> 10 µM → "LOW" |
| `test_ddi_r1_high_value` | R1 = 1.40 → "HIGH" or "1.4" in output |
| `test_ddi_r1_low_no_action` | R1 ≈ 1.0 → "LOW" in output |
| `test_ddi_returns_r1_for_each_cyp` | Multi-CYP: all isoforms reported |
| `test_ivive_all_models` | well_stirred / parallel_tube / dispersion all return str |
| `test_run_nca_calls_r_code` | Mock: verifies `run_r_code` is called, returns str |

### `test_poppk.py` — Population PK Tools

| Test | What it checks |
|---|---|
| `test_generates_1cmt_iv_stream` | NONMEM control stream contains "PROBLEM" or "1cmt" |
| `test_all_model_types_return_string` | Parametrised: 1cmt_iv, 2cmt_iv, 1cmt_oral, 2cmt_oral, emax |
| `test_parse_ofv` | OFV value extracted from .lst file |
| `test_parse_theta_values` | THETA estimates extracted |
| `test_parse_covariance_aborted_flag` | "ABORTED" flagged when covariance step fails |
| `test_parse_missing_file` | Missing .lst → error string, no exception |
| `test_compare_returns_string` | compare_pk_models returns str |
| `test_compare_includes_aic` | "AIC" in output |
| `test_compare_includes_lrt` | LRT or OFV comparison in output |
| `test_compare_flags_failed_cov` | cov_successful=False → "✗" in output |
| `test_compare_recommends_model` | "recommend" or "best" in output |
| `test_nlmixr2_calls_r` | Mock: run_r_code called, result is str |
| `test_mrgsolve_calls_r` | Mock: run_r_code called, result is str |

### `test_bioanalytical.py` — Bioanalytical Tools

| Test | What it checks |
|---|---|
| `test_calibration_curve_returns_string` | Returns str |
| `test_calibration_curve_reports_r_squared` | "R²" or "R2" in output |
| `test_calibration_curve_reports_slope` | "slope" in output |
| `test_calibration_curve_back_calculation_accuracy` | Near-perfect data → PASS in output |
| `test_calibration_curve_all_weightings` | 1/x, 1/x2, none all work |
| `test_calibration_curve_empty_input` | Empty lists → error str, no crash |
| `test_validation_reports_precision` | "precision" or "CV" in output |
| `test_validation_reports_accuracy` | "accuracy" or "bias" in output |
| `test_process_lc_ms_returns_string` | Returns str |
| `test_process_lc_ms_flags_blq` | BLQ samples flagged in output |

### `test_cdisc_io.py` — CDISC I/O Tools

| Test | What it checks |
|---|---|
| `test_valid_dataset_passes` | Complete NONMEM dataset → no error |
| `test_missing_required_column_flagged` | Missing AMT → "AMT" or "missing" in output |
| `test_missing_id_column_flagged` | Missing ID → flagged |
| `test_missing_file_handled` | Nonexistent file → error str, no crash |
| `test_negative_time_flagged` | Negative TIME → returns str without crash |
| `test_sdtm_pc_missing_file` | Missing .xpt → error str |
| `test_pp_domain_from_nca_csv` | NCA results → PP domain str |
| `test_pp_domain_missing_file` | Missing NCA file → error str |

### `test_tool_contracts.py` — Schema Contracts

Parametrised across all 5 PKPD modules:

| Test | What it checks |
|---|---|
| `test_all_public_functions_return_str` | Every function annotates `-> str` |
| `test_all_public_functions_have_docstring` | Every function has a non-empty docstring |
| `test_all_parameters_have_type_hints` | Every parameter has a type annotation |
| `test_no_function_raises_on_import` | Each module has at least 1 public function |
| `test_pkpd_module_count` | Exactly 5 modules are registered |

**Why these matter:** The agent uses `inspect` to build its tool registry. A missing type hint or docstring causes the agent to skip or mis-use a tool.

### `test_pkpd_agent.py` — Agent Configuration

| Test | What it checks |
|---|---|
| `test_enable_langsmith_sets_env_vars` | Sets all 3 LANGCHAIN_* env vars |
| `test_enable_langsmith_accepts_explicit_key` | api_key= arg takes priority |
| `test_enable_langsmith_raises_without_key` | EnvironmentError if no key |
| `test_enable_langsmith_custom_endpoint` | Custom endpoint stored correctly |
| `test_system_context_covers_nca` | "NCA" in PKPD_SYSTEM_CONTEXT |
| `test_system_context_covers_ddi` | "DDI" and "R1" in context |
| `test_system_context_covers_diagnostics` | "VPC" and shrinkage in context |
| `test_system_context_is_nonempty` | Context is > 500 chars |
| `test_pkpd_tool_modules_list` | Exactly the 5 expected module paths |
| `test_pkpd_tool_modules_all_importable` | All 5 modules import without error |
| `test_pkpd_knowhow_files_exist` | know_how/pkpd/ dir has ≥3 .md files |
| `test_nca_knowhow_file_exists` | nca_*.md present |
| `test_dmpk_knowhow_file_exists` | dmpk_*.md present |

---

## CI Pipeline Jobs

```
push to main
│
├── lint        ruff check on all PKPD Python files
│    │
└── test  ←── needs: lint
      │        pytest tests/ with coverage
      │
      └── build  ←── needs: test, only on push
            │
            ├── deploy-lambda
            ├── deploy-fargate
            ├── deploy-ecs-ec2
            └── deploy-eks

schedule (Monday 02:00 UTC)  ← independent of above
└── benchmark                  LangSmith 16-case PKPD evaluation
```

---

## What Is NOT Tested in pytest

These require a real LLM call and run in LangSmith instead:

| Capability | Why not in pytest | Where it's tested |
|---|---|---|
| Agent picks the right tool for a task | Non-deterministic LLM output | LangSmith evaluate() |
| NCA output is pharmacometrically correct | Requires PKNCA (R) + LLM | LangSmith benchmark |
| popPK workflow completes end-to-end | Requires nlmixr2 (R) + LLM | LangSmith benchmark |
| GOF interpretation is correct | Free-text LLM output | LangSmith keyword eval |
| DDI report matches FDA guidance | Free-text LLM output | LangSmith keyword eval |

---

## Adding New Tests

When you add a new tool function, add tests in the corresponding file:

```python
# 1. Test the happy path — expected output contains key terms
def test_my_new_function_happy_path():
    result = my_new_function(valid_inputs)
    assert isinstance(result, str)
    assert "expected_term" in result

# 2. Test the classification/decision
def test_my_new_function_classification():
    result = my_new_function(inputs_that_trigger_high)
    assert "HIGH" in result

# 3. Test graceful error handling
def test_my_new_function_bad_input():
    result = my_new_function(empty_or_invalid_input)
    assert isinstance(result, str)       # never raise
    assert "error" in result.lower()    # but tell the user

# 4. If it uses R, test via mock
def test_my_new_function_calls_r():
    with patch("biomni.tool.mymodule.run_r_code") as mock_r:
        mock_r.return_value = "expected R output"
        result = my_new_function(valid_inputs)
        assert mock_r.called
        assert isinstance(result, str)
```

The contract tests (`test_tool_contracts.py`) will automatically pick up
your new function and check its return type, docstring, and type hints
without any changes needed.

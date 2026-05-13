# Population PK/PD Model Diagnostics and Regulatory Guidance

## Metadata
**short_description**: Complete reference for population PK diagnostic criteria, model evaluation, covariate analysis, and FDA/EMA regulatory requirements
**authors**: Biomni PKPD Team
**license**: MIT
**commercial_use**: yes

## Overview
Population PK (popPK) analysis uses nonlinear mixed-effects models (NLME) to
characterise PK variability across patients. This guide covers model evaluation,
diagnostics, and regulatory submission requirements per FDA and EMA guidance.

## Goodness-of-Fit (GOF) Diagnostics

### DV vs PRED (Population Prediction)
- Should scatter symmetrically around the line of identity (y = x)
- Systematic deviation: structural model misspecification
- Fan-shaped spread: misspecified residual error model (try proportional)
- Over-prediction at low concentrations: consider additive component

### DV vs IPRED (Individual Prediction)
- Tighter scatter than DV vs PRED (IIV captures individual differences)
- Systematic trend after accounting for IIV: residual model misspecification
- If DV vs IPRED poor but DV vs PRED acceptable: IIV poorly estimated

### CWRES vs TIME
- Should show random scatter around zero with no trend
- Trending upward over time: under-prediction in terminal phase (t½ too short)
- U-shaped: distribution phase missing (add peripheral compartment)
- Oscillating: multiple absorption phases (add lag time or transit compartment)

### CWRES vs PRED
- Random scatter around zero
- Funnel (variance increases with PRED): proportional error not captured
- Values outside ±4: potential outliers (investigate before removing)

### NPDE (Normalised Prediction Distribution Error)
- More robust than CWRES for models with BLQ data
- Should follow N(0,1): check QQ-plot and histogram
- Preferred when >20% BLQ or for complex models

## Visual Predictive Check (VPC)

### Interpretation
- 80% of observations should fall within 10th–90th percentile PI
- 50th percentile (median) of PI should track median of observations
- Assess stratified VPC by dose group, time interval, or covariate subgroup

### Common Failures and Solutions
| VPC Finding | Likely Cause | Fix |
|---|---|---|
| PI too narrow (many obs outside) | Under-estimated IIV or error | Increase IIV or error |
| PI too wide | Over-estimated variability | Reduce IIV; check covariates |
| Median PI below observed median | Systematic under-prediction | Increase typical value (THETA) |
| Good early, poor late | Terminal phase misspecification | Add compartment or recheck lambda-z |
| Good at median, poor at extremes | Non-normal IIV distribution | Box-Cox IIV transformation |

### pcVPC vs binless VPC
- pcVPC (prediction-corrected): normalises by predicted value; useful for sparse data
- Use when multiple dose levels overlap in concentration range

## Shrinkage

### ETA Shrinkage
- Shrinkage = 1 - SD(ETA_i) / ω
- > 30%: individual EBEs unreliable for covariate screening
- High shrinkage: sparse data per subject; model predicts population, not individuals
- Resolution: collect more PK samples per subject, or use full random effects model

### EPS Shrinkage
- > 30%: IWRES and DV vs IPRED diagnostics unreliable
- Indicates the model is fitting observations very closely (overfitting risk)

## Model Parameter Quality

### RSE (Relative Standard Error = SE/Estimate × 100%)
| Parameter type | Acceptable RSE |
|---|---|
| Fixed effects (THETA) | < 25% |
| Variance/IIV (OMEGA) | < 50% |
| Residual error (SIGMA) | < 50% |
| Covariance parameters | < 50% |

### Condition Number
- > 10⁷: near-singular matrix; model unstable
- 10⁴–10⁷: borderline; review parameter correlations
- < 10⁴: stable

### Covariance Step
- Must be successful for reliable SE/RSE estimates
- Aborted: simplify model, fix poorly estimated parameters, check initial estimates
- Alternative: bootstrap for CI without covariance step

## Covariate Analysis

### Screening Methods
1. Empirical Bayes estimates (EBEs/ETAs) vs covariates — scatter plots
2. Individual PK parameters vs covariates — non-parametric correlation
3. Note: only valid when ETA shrinkage < 30%

### Statistical Criteria
- Forward addition: ΔOFV > 3.84 (χ², df=1, p<0.05)
- Backward elimination: ΔOFV > 6.63 (χ², df=1, p<0.01)
- Final model: all retained covariates pass backward elimination threshold

### Clinical Relevance Criterion
- FDA standard: covariate causes >20% change in AUC or Cmax at extremes of covariate range
- Dose adjustment warranted if: exposure change >2-fold in patient subgroup

### Standard Covariate Models
- Continuous (power): CL = TVCL × (WT/70)^θ_WT
- Continuous (linear): CL = TVCL × (1 + θ_AGE × (AGE - median_AGE))
- Categorical (shift): CL = TVCL × θ_SEX^SEX (SEX = 0 or 1)
- Renal impairment: CL = TVCL × (CRCL/90)^θ_CRCL

## Regulatory Submission Requirements

### FDA PopPK Guidance (2022 update)
- Describe model-building strategy (structural, statistical, covariate)
- Report all diagnostic plots (GOF, VPC, NPDE)
- Provide parameter estimates with 95% CI (bootstrap or covariance)
- Model-based dose adjustment recommendations with exposure-response evidence
- Simulation-based clinical recommendation

### EMA PopPK Guideline (2007)
- Consistent with FDA: model evaluation, parameter precision, simulation
- Emphasizes external validation when possible (predict new dataset)

### Submission Package Checklist
- [ ] Dataset description (SDTM/ADaM source, inclusion/exclusion)
- [ ] Model code (NONMEM control stream or nlmixr2 script)
- [ ] Parameter table (THETA, OMEGA, SIGMA with RSE%)
- [ ] GOF diagnostic plots (minimum 4: DV/PRED, DV/IPRED, CWRES/TIME, CWRES/PRED)
- [ ] VPC (stratified by dose and/or study if applicable)
- [ ] Covariate analysis results (forest plot of covariate effects)
- [ ] Clinical pharmacology conclusions (dose adjustments, subgroup recommendations)

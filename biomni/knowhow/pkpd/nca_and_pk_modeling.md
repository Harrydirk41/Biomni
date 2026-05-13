# NCA and Pharmacokinetic Modeling Guide

## Metadata
**short_description**: Step-by-step guide for non-compartmental analysis, compartmental PK modeling, and parameter interpretation for drug development
**authors**: Biomni PKPD Team
**license**: MIT
**commercial_use**: yes

## Overview
This document covers the standard workflow for PK data analysis from bioanalytical data
through NCA, compartmental modeling, and clinical PK interpretation.

## Non-Compartmental Analysis (NCA)

### Lambda-z Selection Rules
- Select at least 3 terminal phase concentration-time points
- Points must be on the log-linear decline (post-Cmax)
- R² ≥ 0.9 required for reliable t½ estimate
- Span of data used for lambda-z must be > one half-life
- Do NOT include Cmax in lambda-z unless clearly part of the terminal phase
- Exclude anomalously low concentrations (assay artifact)

### AUC Calculation
- Use linear trapezoidal for ascending phase (Cmin to Cmax)
- Use log trapezoidal for descending phase (Cmax to last measurable concentration)
- AUCinf = AUClast + Clast / lambda-z
- AUC extrapolation > 20%: AUCinf unreliable; report AUClast only
- Missing time points: interpolate if < 2 consecutive missing; exclude subject if pattern is systematic

### Key NCA Parameters
| Parameter | Formula | Interpretation |
|---|---|---|
| Cmax | Direct observation | Peak exposure |
| Tmax | Direct observation | Time to peak |
| AUClast | Trapezoidal sum | Total exposure to last sample |
| AUCinf | AUClast + Clast/λz | Total systemic exposure |
| t½ | ln2 / λz | Time for concentration to halve |
| CL/F | Dose / AUCinf | Apparent clearance (oral) |
| Vd/F | CL/F / λz | Apparent volume of distribution |
| MRT | AUMCinf / AUCinf | Mean residence time |

### IV vs Oral Distinction
- IV: reports absolute CL and Vd (F=1 assumed)
- Oral: reports CL/F and Vd/F (apparent; confounded by bioavailability F)
- To get absolute CL: need IV crossover → F = AUCoral/AUCiv (dose-normalised)

## Compartmental PK Models

### Model Selection Hierarchy
1. Start with the simplest model that describes the data
2. 1-compartment: single exponential decline; monoexponential log-concentration profile
3. 2-compartment: biphasic decline; distribution phase visible after IV bolus
4. 3-compartment: triphasic decline; deep tissue compartment (rare in routine analysis)
5. Use LRT (ΔOFV ≥ 3.84) to justify increased compartment number
6. Confirm with VPC — model must predict observed data distribution

### Oral Absorption Models
- First-order: most common; KA drives absorption rate
- Zero-order: controlled-release formulations
- Lag time: delayed absorption (ALAG1 in NONMEM)
- Transit compartment model: for complex absorption shapes
- Dual absorption: bimodal profiles (enterohepatic recirculation)

### Error Models
- Proportional: variance ∝ IPRED² (most common for PK data spanning >1 log)
- Additive: constant variance (use when low concentrations dominate or near LLOQ)
- Combined: proportional + additive (accounts for assay noise at low concentrations)
- Rule: start with proportional; add additive if CWRES shows trend at low IPRED

### IIV (Inter-Individual Variability)
- Exponential IIV: PAR_i = TVPAR × exp(η_i) — ensures positivity
- η ~ N(0, ω²)
- ω² = variance; %CV ≈ √ω² × 100 for ω < 0.3 (exact: √(exp(ω²)-1) × 100)
- Acceptable: IIV 20–50% CV for CL and Vd in typical drugs
- Large IIV (>100%) suggests model misspecification or outlier subjects

## Clinical PK Interpretation

### Clearance Classification (Human)
| CLh (mL/min/kg) | Extraction Ratio | Classification |
|---|---|---|
| > 13 | > 0.7 | High clearance |
| 6.5–13 | 0.3–0.7 | Medium clearance |
| < 6.5 | < 0.3 | Low clearance |

### Steady-State Considerations
- Steady state reached after ~5 half-lives
- Accumulation ratio (R) = AUCss / AUCsingle = 1 / (1 - e^(-λz×τ))
- For t½ >> dosing interval: substantial accumulation
- Cmax,ss / Cmin,ss ratio = peak-to-trough fluctuation

### Food Effect Assessment
- Fed/fasted: compare AUC and Cmax ratios (90% CI within 80–125% = no effect)
- High fat meal: can increase or decrease Cmax depending on mechanism
- Dissolution-limited absorption: food often increases AUC
- High extraction drugs: food → increased hepatic blood flow → increased bioavailability

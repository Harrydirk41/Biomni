# DMPK Assay Interpretation and DDI Risk Assessment

## Metadata
**short_description**: Industry-standard thresholds and interpretation rules for ADME assays, CYP inhibition, and FDA/EMA DDI risk assessment
**authors**: Biomni PKPD Team
**license**: MIT
**commercial_use**: yes

## Overview
Covers interpretation thresholds for the standard in vitro DMPK panel used in
drug discovery and development, including FDA/EMA DDI assessment workflow.

## Metabolic Stability (Microsomal / Hepatocyte)

### Clearance Classification (Human Liver Microsomes)
| In vitro t½ | CLint class | In vivo prediction |
|---|---|---|
| < 30 min | HIGH | Rapid first-pass; likely low oral F |
| 30–60 min | MEDIUM | Moderate hepatic extraction |
| > 60 min | LOW | Good stability; low extraction |

### Key Caveats
- Compound concentration should be < Km (typically << 1 µM for CYP substrates)
- Substrate depletion > 80% at t=0: suspect t=0 instability or non-specific binding
- Non-linear decay (R² < 0.9): biphasic; consider bi-exponential fit or multiple CYP
- Human vs animal scaling: rat HLM CLint often 2–5× higher than human HLM
- Hepatocytes more predictive for glucuronidation, sulfation, and aldehyde oxidase

### MPPGL Values (mg microsomal protein per gram liver)
| Species | MPPGL | Liver weight (g/kg) |
|---|---|---|
| Human | 45 | 20.7 |
| Rat | 60 | 40.0 |
| Mouse | 65 | 88.0 |
| Dog | 77 | 32.0 |
| Monkey | 48 | 26.0 |

## Plasma Protein Binding (PPB)

### fu Classification
| fu,plasma | Classification | Implication |
|---|---|---|
| > 0.5 | Low binding | Large free drug fraction |
| 0.1–0.5 | Moderate binding | — |
| 0.01–0.1 | High binding | Small errors in fu → large CLh error |
| < 0.01 | Very high binding | IVIVE particularly sensitive; re-test |

### Restrictive vs Non-Restrictive Clearance
- Low extraction drugs (Eh < 0.3): CLh sensitive to fu changes → PPB-driven DDI risk
- High extraction drugs (Eh > 0.7): CLh ≈ Qh; insensitive to fu (non-restrictive)
- Unbound drug hypothesis: target engagement determined by unbound concentration

## Permeability

### Caco-2 Classification
| Papp A→B (×10⁻⁶ cm/s) | Class | Expected absorption |
|---|---|---|
| ≥ 10 | High | > 80% absorbed |
| 1–10 | Moderate | Variable |
| < 1 | Low | Poor oral absorption |

### Efflux Ratio Interpretation
- ER = Papp B→A / Papp A→B
- ER ≥ 2.0: active efflux suspected (P-gp, BCRP, MRP2)
- Confirm with P-gp inhibitor (GF120918 / elacridar at 2 µM)
- P-gp substrate: limited CNS penetration, possible food effect on absorption
- ER < 0.5: possible active influx (OATP, PEPT1)

### PAMPA vs Caco-2
- PAMPA: passive permeability only; no transporter involvement
- Caco-2: passive + active transport; more predictive for drugs with transporters
- If Papp(PAMPA) >> Papp(Caco-2): active efflux
- If Papp(Caco-2) >> Papp(PAMPA): active influx

## CYP Inhibition

### Risk Thresholds (FDA 2020)
- R1 = 1 + [I]max,u / IC50 (basic model)
- R1 ≥ 1.02: follow-up required
- R1 ≥ 2.0: clinical DDI study required

### IC50 Classification (General)
| IC50 (µM) | Risk |
|---|---|
| < 1 | HIGH — clinical DDI study likely required |
| 1–10 | MODERATE — evaluate against clinical [I]u,max |
| > 10 | LOW — usually no clinical DDI concern |

### Time-Dependent Inhibition (TDI)
- IC50 shift ratio = IC50(no preincubation) / IC50(30-min preincubation)
- Shift ratio ≥ 1.5: TDI detected → mechanism-based inhibition (MBI)
- TDI warrants kinact/KI experiment → R1,TDI = 1 + (kinact × [I]max,u) / (KI + [I]max,u) × (1/kdeg)
- MBI compounds: irreversible; DDI risk persists after drug washout

### Major CYP Isoforms and Clinical Substrates
| CYP | Key substrates | Clinical relevance |
|---|---|---|
| CYP3A4 | midazolam, triazolam, simvastatin | ~50% of marketed drugs |
| CYP2D6 | codeine, metoprolol, fluoxetine | polymorphic; PM/EM phenotypes |
| CYP2C9 | warfarin, diclofenac, glipizide | narrow therapeutic index substrates |
| CYP2C19 | omeprazole, clopidogrel | polymorphic; clinical relevance |
| CYP1A2 | caffeine, theophylline, clozapine | induction by smoking |

## DDI Risk Assessment Framework

### FDA 2020 Inhibition Workflow
1. Run in vitro IC50 for each CYP with the drug as perpetrator
2. Calculate [I]max,u = Cmax,total × fu,plasma (at highest clinical dose)
3. Calculate R1 = 1 + [I]max,u / IC50
4. R1 < 1.02: no further action for that CYP
5. R1 1.02–2.0: conduct mechanistic static model or in vivo DDI
6. R1 ≥ 2.0: clinical DDI study required
7. For oral drugs: also calculate R2 (gut) for CYP3A substrates

### Induction Assessment (separate from inhibition)
- mRNA fold-change ≥ 2-fold at 1/3 Cmax,u → conduct dynamic model
- AhR (CYP1A2): omeprazole positive control
- PXR (CYP3A4): rifampicin positive control
- CYP3A4 induction at clinical concentrations: net effect depends on inhibition vs induction

### Transporter DDI
- P-gp, BCRP: assess as substrate and inhibitor (Caco-2 bidirectional)
- OATP1B1/1B3: hepatic uptake; relevance for statins, SN-38
- OCT2, MATE1/2K: renal secretion; relevance for metformin
- FDA thresholds: [I]max,total / IC50 ≥ 0.1 (P-gp, BCRP); ≥ 0.04 (OATP1B1)

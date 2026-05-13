#!/usr/bin/env Rscript
# Install all R packages required for Biomni PKPD tools.
# Run once after setting up the conda environment:
#   conda activate biomni_e1
#   Rscript pkpd_env/install_r_packages.R

cat("Installing Biomni PKPD R packages...\n")
cat("This will take 10–20 minutes on first run.\n\n")

# CRAN mirror
options(repos = c(CRAN = "https://cloud.r-project.org"))

install_if_missing <- function(pkg, ...) {
  if (!requireNamespace(pkg, quietly = TRUE)) {
    cat(sprintf("Installing %s ...\n", pkg))
    install.packages(pkg, ...)
  } else {
    cat(sprintf("  %s already installed.\n", pkg))
  }
}

# ── Core PKPD packages ─────────────────────────────────────────────────────

# NCA
install_if_missing("PKNCA")

# Population PK fitting engine (ODE solver)
install_if_missing("rxode2")

# Population PK fitting — nlmixr2 and extensions
install_if_missing("nlmixr2")
install_if_missing("nlmixr2extra")   # covariate testing, bootstrap

# Population PK simulation
install_if_missing("mrgsolve")

# Diagnostics and visualisation
install_if_missing("xpose")            # GOF plots for nlmixr2/NONMEM
install_if_missing("xpose.nlmixr2")   # nlmixr2 adapter for xpose
install_if_missing("vpc")             # Visual predictive check
install_if_missing("ggPMX")           # GOF plots for NONMEM/nlmixr2

# ── Data handling ──────────────────────────────────────────────────────────
install_if_missing("dplyr")
install_if_missing("tidyr")
install_if_missing("purrr")
install_if_missing("readr")
install_if_missing("data.table")
install_if_missing("haven")           # Read SAS .xpt files (CDISC)
install_if_missing("pyreadstat")      # Alternative SAS reader (via reticulate)

# ── Visualisation ──────────────────────────────────────────────────────────
install_if_missing("ggplot2")
install_if_missing("patchwork")       # Combine ggplot panels
install_if_missing("ggrepel")         # Non-overlapping labels
install_if_missing("GGally")          # Pairs plots for ETA vs covariate

# ── Statistics and curve fitting ──────────────────────────────────────────
install_if_missing("nlme")            # Nonlinear mixed effects (base R)
install_if_missing("minpack.lm")      # Levenberg-Marquardt NLS
install_if_missing("drc")             # Dose-response curve fitting

# ── PBPK and physiological modelling ─────────────────────────────────────
install_if_missing("PKPDsim")         # Flexible PK/PD simulation

# ── Reporting ─────────────────────────────────────────────────────────────
install_if_missing("knitr")
install_if_missing("rmarkdown")
install_if_missing("flextable")       # Publication-quality tables

# ── Bioconductor (optional, for omics-PK integration) ─────────────────────
if (!requireNamespace("BiocManager", quietly = TRUE)) {
  install.packages("BiocManager")
}

cat("\n=== Verification ===\n")
pkgs <- c("PKNCA", "rxode2", "nlmixr2", "mrgsolve",
          "xpose", "vpc", "ggplot2", "dplyr", "haven")
for (pkg in pkgs) {
  status <- if (requireNamespace(pkg, quietly = TRUE)) "✓" else "✗ FAILED"
  cat(sprintf("  %s  %s\n", status, pkg))
}

cat("\nBiomni PKPD R package installation complete.\n")
cat("Activate with: conda activate biomni_e1\n")

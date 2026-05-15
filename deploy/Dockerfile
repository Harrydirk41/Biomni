# syntax=docker/dockerfile:1.4
# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: build the conda environment in a layer-cache-friendly way
# ─────────────────────────────────────────────────────────────────────────────
FROM continuumio/miniconda3:24.1.2-0 AS env-builder

WORKDIR /build

# Copy only the env spec first so Docker caches this layer until env changes
COPY biomni_env/environment.yml .

RUN conda env create -f environment.yml \
 && conda clean -afy \
 && find /opt/conda/envs/biomni_e1 -name "*.pyc" -delete

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: final runtime image
# ─────────────────────────────────────────────────────────────────────────────
FROM continuumio/miniconda3:24.1.2-0

LABEL org.opencontainers.image.source="https://github.com/Harrydirk41/Biomni"
LABEL org.opencontainers.image.description="Biomni biomedical AI agent"

# Copy built environment from stage 1
COPY --from=env-builder /opt/conda/envs/biomni_e1 /opt/conda/envs/biomni_e1

ENV PATH=/opt/conda/envs/biomni_e1/bin:$PATH \
    CONDA_DEFAULT_ENV=biomni_e1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Biomni package (source code layer — changes often, kept separate)
COPY biomni/           ./biomni/
COPY pyproject.toml    ./
COPY MANIFEST.in       ./
RUN /opt/conda/envs/biomni_e1/bin/pip install --no-cache-dir -e . --no-deps \
 && /opt/conda/envs/biomni_e1/bin/pip install --no-cache-dir boto3 \
 && /opt/conda/envs/biomni_e1/bin/python -c "import boto3; from biomni.llm import _BedrockChat; print('Bedrock OK')"

# Copy deployment helpers
COPY deploy/app.py            ./app.py
COPY deploy/lambda_handler.py ./lambda_handler.py

EXPOSE 8000 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

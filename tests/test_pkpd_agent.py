"""Unit tests for PKPDAgent initialization and configuration.

These tests never make LLM API calls — they mock the parent A1.__init__
and verify the PKPD-specific setup logic.
"""

import os
import pytest
from unittest.mock import patch


# ─────────────────────────────────────────────────────────────────────────────
# enable_langsmith helper
# ─────────────────────────────────────────────────────────────────────────────

from biomni.agent.pkpd_agent import enable_langsmith


def test_enable_langsmith_sets_env_vars(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_API_KEY", "ls__test_key")
    enable_langsmith(project="test-project")
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGCHAIN_PROJECT"] == "test-project"
    assert os.environ["LANGCHAIN_API_KEY"] == "ls__test_key"


def test_enable_langsmith_accepts_explicit_key(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    enable_langsmith(api_key="ls__explicit_key", project="proj")
    assert os.environ["LANGCHAIN_API_KEY"] == "ls__explicit_key"


def test_enable_langsmith_raises_without_key(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="No LangSmith API key"):
        enable_langsmith()


def test_enable_langsmith_raises_if_not_installed(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_API_KEY", "ls__key")
    with patch.dict("sys.modules", {"langsmith": None}):
        with pytest.raises((ImportError, TypeError)):
            enable_langsmith(api_key="ls__key")


def test_enable_langsmith_custom_endpoint(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_API_KEY", "ls__key")
    enable_langsmith(endpoint="https://my.langsmith.internal")
    assert os.environ["LANGCHAIN_ENDPOINT"] == "https://my.langsmith.internal"


# ─────────────────────────────────────────────────────────────────────────────
# PKPD_SYSTEM_CONTEXT content
# ─────────────────────────────────────────────────────────────────────────────

from biomni.agent.pkpd_agent import PKPD_SYSTEM_CONTEXT  # noqa: E402


def test_system_context_covers_nca():
    assert "NCA" in PKPD_SYSTEM_CONTEXT or "non-compartmental" in PKPD_SYSTEM_CONTEXT.lower()


def test_system_context_covers_dmpk():
    assert "DMPK" in PKPD_SYSTEM_CONTEXT or "microsomal" in PKPD_SYSTEM_CONTEXT.lower()


def test_system_context_covers_ddi():
    assert "DDI" in PKPD_SYSTEM_CONTEXT
    assert "R1" in PKPD_SYSTEM_CONTEXT


def test_system_context_covers_regulatory():
    assert "FDA" in PKPD_SYSTEM_CONTEXT


def test_system_context_covers_diagnostics():
    assert "VPC" in PKPD_SYSTEM_CONTEXT
    assert "GOF" in PKPD_SYSTEM_CONTEXT or "shrinkage" in PKPD_SYSTEM_CONTEXT.lower()


def test_system_context_is_nonempty():
    assert len(PKPD_SYSTEM_CONTEXT.strip()) > 500


# ─────────────────────────────────────────────────────────────────────────────
# PKPDAgent tool module list
# ─────────────────────────────────────────────────────────────────────────────

from biomni.agent.pkpd_agent import PKPDAgent  # noqa: E402


def test_pkpd_tool_modules_list():
    expected = {
        "biomni.tool.dmpk",
        "biomni.tool.poppk",
        "biomni.tool.pbpk",
        "biomni.tool.bioanalytical",
        "biomni.tool.cdisc_io",
    }
    assert set(PKPDAgent.PKPD_TOOL_MODULES) == expected


def test_pkpd_tool_modules_all_importable():
    import importlib
    for module_path in PKPDAgent.PKPD_TOOL_MODULES:
        mod = importlib.import_module(module_path)
        assert mod is not None


# ─────────────────────────────────────────────────────────────────────────────
# Know-how skill files exist
# ─────────────────────────────────────────────────────────────────────────────

def test_pkpd_knowhow_files_exist():
    import os
    import biomni.agent.pkpd_agent as pkg
    knowhow_dir = os.path.normpath(
        os.path.join(os.path.dirname(pkg.__file__), "..", "know_how", "pkpd")
    )
    assert os.path.isdir(knowhow_dir), f"PKPD know-how directory not found: {knowhow_dir}"
    md_files = [f for f in os.listdir(knowhow_dir) if f.endswith(".md")]
    assert len(md_files) >= 3, f"Expected at least 3 .md files, found: {md_files}"


def test_nca_knowhow_file_exists():
    import os
    import biomni.agent.pkpd_agent as pkg
    knowhow_dir = os.path.normpath(
        os.path.join(os.path.dirname(pkg.__file__), "..", "know_how", "pkpd")
    )
    files = os.listdir(knowhow_dir)
    assert any("nca" in f.lower() for f in files)


def test_dmpk_knowhow_file_exists():
    import os
    import biomni.agent.pkpd_agent as pkg
    knowhow_dir = os.path.normpath(
        os.path.join(os.path.dirname(pkg.__file__), "..", "know_how", "pkpd")
    )
    files = os.listdir(knowhow_dir)
    assert any("dmpk" in f.lower() for f in files)

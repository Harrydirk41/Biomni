"""Contract tests for all PKPD tool modules.

Verifies that every public function:
- Returns str  (Biomni agent convention)
- Has a docstring  (agent uses these for tool selection)
- Has type hints on all parameters  (agent uses these for argument construction)

These tests do not call any function — they only inspect signatures and
annotations. They run in milliseconds and require no dependencies beyond
the standard library.
"""

import inspect
import pytest
import importlib


PKPD_MODULES = [
    "biomni.tool.dmpk",
    "biomni.tool.poppk",
    "biomni.tool.pbpk",
    "biomni.tool.bioanalytical",
    "biomni.tool.cdisc_io",
]


def get_public_functions(module):
    return [
        (name, obj)
        for name, obj in inspect.getmembers(module, inspect.isfunction)
        if not name.startswith("_") and obj.__module__ == module.__name__
    ]


@pytest.fixture(params=PKPD_MODULES)
def pkpd_module(request):
    return importlib.import_module(request.param)


def test_all_public_functions_return_str(pkpd_module):
    for name, fn in get_public_functions(pkpd_module):
        hints = fn.__annotations__
        assert hints.get("return") is str, (
            f"{pkpd_module.__name__}.{name} must annotate return type as str "
            f"(Biomni tool convention). Got: {hints.get('return')}"
        )


def test_all_public_functions_have_docstring(pkpd_module):
    for name, fn in get_public_functions(pkpd_module):
        assert fn.__doc__ and fn.__doc__.strip(), (
            f"{pkpd_module.__name__}.{name} is missing a docstring. "
            "The agent uses docstrings to decide which tool to call."
        )


def test_all_parameters_have_type_hints(pkpd_module):
    for name, fn in get_public_functions(pkpd_module):
        sig = inspect.signature(fn)
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            assert param.annotation != inspect.Parameter.empty, (
                f"{pkpd_module.__name__}.{name}({param_name}) is missing a type hint. "
                "The agent needs type hints to construct tool arguments correctly."
            )


def test_no_function_raises_on_import(pkpd_module):
    # Just importing and getting members should never raise
    fns = get_public_functions(pkpd_module)
    assert len(fns) > 0, f"{pkpd_module.__name__} has no public functions"


def test_pkpd_module_count():
    # Sanity check: make sure we have the right number of modules
    assert len(PKPD_MODULES) == 5

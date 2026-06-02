"""Smoke test: every top-level module imports without side effects."""
import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "skillopt.config",
        "skillopt.model.router",
        "skillopt.model.backend_config",
        "skillopt.envs",
    ],
)
def test_module_imports(module):
    importlib.import_module(module)


@pytest.mark.parametrize(
    "module",
    [
        "skillopt.envs.dataverse.scripts.setup_live_env",
        "skillopt.envs.dataverse.scripts.promote",
    ],
)
def test_dataverse_script_imports(module):
    importlib.import_module(module)

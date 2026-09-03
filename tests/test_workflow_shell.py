"""Pin that workflow shell blocks expand the variables they declare.

A remediation pass once rewrote every double quote in live-tests.yml to a
single quote. The file stayed valid YAML and the whole Python gate stayed
green, but every ``$VAR`` became a literal and the workflow could not run
a single step. Nothing in the repository reads the shell inside a
workflow, so this test is the only thing that can see it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).parent.parent / ".github" / "workflows").glob("*.yml"))


def _steps_with_env(path: Path) -> list[tuple[str, dict[str, str], str]]:
    """Yield (step name, declared env, run script) for every step with both."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    found: list[tuple[str, dict[str, str], str]] = []
    for job in (document.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            env = step.get("env")
            script = step.get("run")
            if env and script:
                found.append((step.get("name", "<unnamed>"), env, script))
    return found


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_declared_env_vars_are_expanded_not_quoted_literally(path: Path) -> None:
    """A step that declares an env var must reference it expandably.

    A reference inside single quotes is the literal text, not the value.
    That is a silent, total failure of the step, so it is worth failing
    the suite over.
    """
    for name, env, script in _steps_with_env(path):
        for var in env:
            literal = re.compile(rf"'[^'\n]*\${var}\b[^'\n]*'")
            match = literal.search(script)
            assert match is None, (
                f"{path.name}, step {name!r}: ${var} appears inside single quotes "
                f"({match.group(0)!r} if matched), where the shell will not expand it. "
                "Use double quotes."
            )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_declares_an_env_var_it_never_uses(path: Path) -> None:
    """A declared but unreferenced env var is dead wiring.

    An unused control in a workflow reads as implemented behaviour that is
    not there, which is how read_against_live_data survived review once.
    """
    for name, env, script in _steps_with_env(path):
        for var in env:
            assert f"${var}" in script or f"${{{var}}}" in script, (
                f"{path.name}, step {name!r}: declares {var} in env but never references it"
            )

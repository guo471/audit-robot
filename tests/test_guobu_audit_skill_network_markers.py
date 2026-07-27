import ast
import importlib.util
from pathlib import Path


SCRIPTS = Path.home() / ".codex" / "skills" / "auditing-guobu-orders" / "scripts"


def _load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _network_markers(name: str) -> tuple[str, ...]:
    tree = ast.parse((SCRIPTS / f"{name}.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "NETWORK_MARKERS"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"NETWORK_MARKERS not found in {name}")


def test_http_500_is_treated_as_retryable_model_service_failure():
    row = {
        "manual_reason": "MODEL_UNCERTAIN: HTTPError: HTTP Error 500: Internal Server Error",
        "manual_reason_cn": "",
        "strategy": "error_to_manual",
    }

    assert _load("select_guobu_tasks").network_failure(row)
    assert "http error 500" in _network_markers("merge_guobu_audit_results")

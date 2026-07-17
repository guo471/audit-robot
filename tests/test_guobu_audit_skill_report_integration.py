import json
import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = Path(os.environ.get(
    "GUOBU_AUDIT_BATCH_WRAPPER",
    Path.home() / ".codex/skills/auditing-guobu-orders/scripts/run_guobu_audit_batch.ps1",
))


def wrapper_text():
    return WRAPPER.read_text(encoding="utf-8")


def compact(text):
    return "".join(text.split()).lower()


def run_plan(project_root, tasks_dir, *extra):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WRAPPER),
         "-ProjectRoot", str(project_root), "-TasksDir", str(tasks_dir),
         "-RunName", "integration_contract", "-PlanOnly", *extra],
        text=True, capture_output=True, timeout=30,
    )


def test_report_format_defaults_to_business_and_plan_exposes_selection(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.json").write_text("{}", encoding="utf-8")
    completed = run_plan(PROJECT_ROOT, tasks)
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["reportFormat"] == "business"
    assert plan["reportGenerator"] == str(PROJECT_ROOT / "tools/guobu_audit_report.py")

    completed = run_plan(PROJECT_ROOT, tasks, "-ReportFormat", "legacy")
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["reportFormat"] == "legacy"
    assert '[ValidateSet("business", "legacy")][string]$ReportFormat = "business"' in wrapper_text()


def test_missing_selected_generator_fails_before_audit_even_in_plan_only(tmp_path):
    project = tmp_path / "project"
    tasks = project / "tasks"
    tools = project / "tools"
    tasks.mkdir(parents=True)
    tools.mkdir()
    (tasks / "one.json").write_text("{}", encoding="utf-8")
    (tools / "run_guobu_model_audit_v2.py").write_text("raise AssertionError", encoding="utf-8")
    completed = run_plan(project, tasks)
    assert completed.returncode != 0
    assert "Missing business report generator" in completed.stderr


def test_business_and_legacy_generators_keep_their_distinct_cli_contracts():
    text = wrapper_text()
    dense = compact(text)
    assert '$businessgenerator=join-path$projectpath"tools\\guobu_audit_report.py"' in dense
    assert '$legacymerger=join-path$skillroot"scripts\\merge_guobu_audit_results.py"' in dense
    assert 'if($reportformat-eq"business")' in dense
    assert '"--first-jsonl",$firstjsonl' in dense
    assert '"--output-xlsx",$combinedxlsx' in dense
    assert '"--output-json",$combinedjson' in dense
    assert '$reportargs+=@("--retry-jsonl",$secondjsonl,"--retry-selection-json",$selectionsummary)' in dense
    assert '$reportargs+=@("--second-jsonl",$secondjsonl)' in dense
    assert '"--overwrite"' in dense


def test_retry_directory_is_run_scoped_cleaned_and_selector_count_controls_rerun():
    dense = compact(wrapper_text())
    assert '$expectedretrytasks=join-path$temproot($runname+"_network_retry_tasks")' in dense
    assert '$retrytasksresolved.startswith($temprootresolved+' in dense
    assert 'remove-item-literalpath$retrytasks-recurse-force' in dense
    assert '$retrycount=[int]$selection.selected' in dense
    assert 'if($retrycount-gt0)' in dense
    assert 'get-childitem-literalpath$retrytasks' not in dense


def test_business_retry_selection_is_only_passed_with_nonempty_retry_jsonl():
    dense = compact(wrapper_text())
    guarded = 'if(-not[string]::isnullorwhitespace($secondjsonl))'
    assert guarded in dense
    block = dense[dense.index(guarded):]
    assert block.index('--retry-jsonl') < block.index('--retry-selection-json')


def test_prices_are_forwarded_only_as_one_numeric_triplet_without_secrets():
    text = wrapper_text()
    dense = compact(text)
    for env_name, option in [
        ("QWEN_INPUT_PRICE_PER_MILLION", "--input-price-per-million"),
        ("QWEN_CACHED_INPUT_PRICE_PER_MILLION", "--cached-input-price-per-million"),
        ("QWEN_OUTPUT_PRICE_PER_MILLION", "--output-price-per-million"),
    ]:
        assert f"$env:{env_name}".lower() in dense
        assert option in text
    assert '$pricesvalid=$true' in dense
    assert 'tryparse' in dense
    assert '$parsed-lt0' in dense
    assert '.tostring("r",[globalization.cultureinfo]::invariantculture)' in dense
    assert 'if($pricesvalid)' in dense
    assert "VISION_API_KEY" not in "\n".join(
        line for line in text.splitlines() if "Write-" in line or "Tee-Object" in line
    )

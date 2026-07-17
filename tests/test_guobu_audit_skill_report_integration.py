import json
import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHARED_WRAPPER = Path(os.environ.get(
    "GUOBU_AUDIT_BATCH_WRAPPER",
    Path.home() / ".codex/skills/auditing-guobu-orders/scripts/run_guobu_audit_batch.ps1",
))
PROJECT_WRAPPER = PROJECT_ROOT / "tools/run_guobu_audit_batch.ps1"
WRAPPER = PROJECT_WRAPPER


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


def make_stub_project(tmp_path, output_order_ids):
    project = tmp_path / "project"; tools = project / "tools"; tasks = project / "tasks"
    tools.mkdir(parents=True); tasks.mkdir()
    (tasks / "one.json").write_text(json.dumps({"channel_order_no": "order-1"}), encoding="utf-8")
    for name in ("run_guobu_audit_batch.ps1", "select_guobu_tasks.py",
                 "guobu_audit_contract.py"):
        (tools / name).write_bytes((PROJECT_ROOT / "tools" / name).read_bytes())
    (tools / "run_guobu_model_audit_v2.py").write_text(
        "import argparse,json\nfrom pathlib import Path\np=argparse.ArgumentParser();"
        "p.add_argument('--out-dir');p.add_argument('--tasks-dir');p.add_argument('--cache-dir');"
        "a,_=p.parse_known_args();out=Path(a.out_dir);out.mkdir(parents=True,exist_ok=True);"
        f"items={[{'row': {'id': x, 'manual_reason': '', 'manual_flag': False}} for x in output_order_ids]!r};"
        "(out/'result.jsonl').write_text(''.join(json.dumps(x)+'\\n' for x in items),encoding='utf-8')",
        encoding="utf-8")
    (tools / "guobu_audit_report.py").write_text(
        "import argparse,json\nfrom pathlib import Path\np=argparse.ArgumentParser();"
        "p.add_argument('--output-xlsx');p.add_argument('--output-json');a,_=p.parse_known_args();"
        "Path(__file__).parents[1].joinpath('report-invoked.txt').write_text('yes');"
        "Path(a.output_xlsx).write_bytes(b'x');Path(a.output_json).write_text(json.dumps({'summary':{}}))",
        encoding="utf-8")
    return project, tasks


def invoke_offline_wrapper(project, tasks, run_name):
    env = os.environ.copy(); env.update(VISION_API_BASE_URL="https://offline.invalid", VISION_API_KEY="dummy")
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(project / "tools/run_guobu_audit_batch.ps1"), "-ProjectRoot", str(project),
        "-TasksDir", str(tasks), "-RunName", run_name], text=True, capture_output=True,
        timeout=30, env=env)


def test_plan_exposes_only_business_report_generator(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.json").write_text("{}", encoding="utf-8")
    completed = run_plan(PROJECT_ROOT, tasks)
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["reportGenerator"] == str(PROJECT_ROOT / "tools/guobu_audit_report.py")
    assert "reportFormat" not in plan


def test_missing_selected_generator_fails_before_audit_even_in_plan_only(tmp_path):
    project = tmp_path / "project"
    tasks = project / "tasks"
    tools = project / "tools"
    tasks.mkdir(parents=True)
    tools.mkdir()
    for name in ("run_guobu_audit_batch.ps1", "select_guobu_tasks.py",
                 "guobu_audit_contract.py"):
        (tools / name).write_bytes((PROJECT_ROOT / "tools" / name).read_bytes())
    (tasks / "one.json").write_text("{}", encoding="utf-8")
    (tools / "run_guobu_model_audit_v2.py").write_text("raise AssertionError", encoding="utf-8")
    completed = run_plan(project, tasks)
    assert completed.returncode != 0
    assert "Missing business report generator" in completed.stderr


def test_business_generator_is_the_only_report_contract():
    text = wrapper_text()
    dense = compact(text)
    assert '$businessgenerator=join-path$projectpath"tools\\guobu_audit_report.py"' in dense
    assert "legacy" not in dense
    assert "merge_guobu_audit_results.py" not in dense
    assert not (PROJECT_ROOT / "tools/merge_guobu_audit_results.py").exists()
    assert '"--first-jsonl",$firstjsonl' in dense
    assert '"--output-xlsx",$combinedxlsx' in dense
    assert '"--output-json",$combinedjson' in dense
    assert '$reportargs+=@("--retry-jsonl",$secondjsonl,"--retry-selection-json",$selectionsummary)' in dense
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


def test_shared_wrapper_is_thin_delegate_to_versioned_project_wrapper():
    shared = compact(SHARED_WRAPPER.read_text(encoding="utf-8"))
    assert 'tools\\run_guobu_audit_batch.ps1' in shared
    assert '&$projectwrapper@forwardparams' in shared
    assert 'invoke-auditrun' not in shared
    assert "legacy" not in shared
    assert PROJECT_WRAPPER.is_file()


def test_project_selector_detects_all_formal_network_failures(tmp_path):
    order_ids = ["timeout-order", "connection-order", "481173059937323224268859"]
    tasks = tmp_path / "tasks"; tasks.mkdir()
    for order_id in order_ids:
        (tasks / f"{order_id}.json").write_text(
            json.dumps({"channel_order_no": order_id}), encoding="utf-8")
    first = tmp_path / "first.jsonl"
    failures = [
        {"_error": "TimeoutError: request timed out", "row": {"id": order_ids[0]}},
        {"row": {"id": order_ids[1], "manual_reason": "ModelConnectionError"}},
        {"_error": "http.client.RemoteDisconnected: Remote end closed connection without response",
         "row": {"id": order_ids[2]}},
    ]
    first.write_text("".join(json.dumps(x) + "\n" for x in failures), encoding="utf-8")
    out = tmp_path / "selected"; summary = tmp_path / "selection.json"
    completed = subprocess.run([os.sys.executable, str(PROJECT_ROOT / "tools/select_guobu_tasks.py"),
        "--source-dir", str(tasks), "--out-dir", str(out), "--timeout-jsonl", str(first),
        "--summary-json", str(summary)], text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(summary.read_text(encoding="utf-8"))["selected"] == 3
    assert len(list(out.glob("*.json"))) == 3


def test_partial_first_jsonl_fails_before_retry_and_report(tmp_path):
    project, tasks = make_stub_project(tmp_path, output_order_ids=["order-1"])
    (tasks / "two.json").write_text(json.dumps({"channel_order_no": "order-2"}), encoding="utf-8")
    completed = invoke_offline_wrapper(project, tasks, "partial-first")
    assert completed.returncode != 0
    assert "First-run completeness validation failed" in completed.stderr
    assert not (project / "report-invoked.txt").exists()


def test_stale_retry_task_cannot_trigger_second_audit_without_network_failure(tmp_path):
    project = tmp_path / "project"
    tools = project / "tools"
    tasks = project / "tasks"
    tools.mkdir(parents=True)
    for name in ("run_guobu_audit_batch.ps1", "select_guobu_tasks.py",
                 "guobu_audit_contract.py"):
        (tools / name).write_bytes((PROJECT_ROOT / "tools" / name).read_bytes())
    tasks.mkdir()
    (tasks / "one.json").write_text(
        json.dumps({"channel_order_no": "order-1"}), encoding="utf-8")

    invocation_log = project / "model-invocations.txt"
    (tools / "run_guobu_model_audit_v2.py").write_text(
        """import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--tasks-dir'); p.add_argument('--out-dir'); p.add_argument('--cache-dir')
p.add_argument('--model'); p.add_argument('--mode'); p.add_argument('--workers')
p.add_argument('--no-targeted-sn-review', action='store_true')
a = p.parse_args()
log = Path(__file__).parents[1] / 'model-invocations.txt'
with log.open('a', encoding='utf-8') as f: f.write(a.tasks_dir + '\\n')
out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
item = {'row': {'id': 'order-1', 'manual_reason': '', 'manual_reason_cn': '',
                'strategy': '', 'manual_flag': False}}
(out / 'result.jsonl').write_text(json.dumps(item) + '\\n', encoding='utf-8')
""", encoding="utf-8")
    (tools / "guobu_audit_report.py").write_text(
        """import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--first-jsonl'); p.add_argument('--retry-jsonl')
p.add_argument('--retry-selection-json'); p.add_argument('--output-xlsx')
p.add_argument('--output-json'); p.add_argument('--overwrite', action='store_true')
a, _ = p.parse_known_args()
Path(a.output_xlsx).write_bytes(b'xlsx-placeholder')
Path(a.output_json).write_text(json.dumps({'summary': {'stub': True}}), encoding='utf-8')
""", encoding="utf-8")

    run_name = "no_network_retry_regression"
    retry_tasks = project / "temp" / f"{run_name}_network_retry_tasks"
    retry_tasks.mkdir(parents=True)
    stale = retry_tasks / "stale.json"
    stale.write_text(json.dumps({"channel_order_no": "stale-order"}), encoding="utf-8")
    env = os.environ.copy()
    env.update(VISION_API_BASE_URL="https://offline.invalid", VISION_API_KEY="dummy")
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WRAPPER),
         "-ProjectRoot", str(project), "-TasksDir", str(tasks), "-RunName", run_name],
        text=True, capture_output=True, timeout=30, env=env,
    )

    assert completed.returncode == 0, completed.stderr
    assert invocation_log.read_text(encoding="utf-8").splitlines() == [str(tasks)]
    selection = json.loads((project / "temp" / f"{run_name}_network_retry_selection.json")
                           .read_text(encoding="utf-8"))
    assert selection["selected"] == 0
    assert not stale.exists()
    second_out = project / "reports" / "model_audit" / f"{run_name}_network_rerun"
    assert not list(second_out.glob("*.jsonl"))

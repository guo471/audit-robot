import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHARED_WRAPPER = Path(os.environ.get(
    "GUOBU_AUDIT_BATCH_WRAPPER",
    Path.home() / ".codex/skills/auditing-guobu-orders/scripts/run_guobu_audit_batch.ps1",
))
PROJECT_WRAPPER = PROJECT_ROOT / "tools/run_guobu_audit_batch.ps1"
WRAPPER = PROJECT_WRAPPER
PROJECT_PYTHON = Path(sys.executable).resolve()


SUBPROCESS_TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


def require_shared_wrapper():
    if not SHARED_WRAPPER.is_file():
        pytest.skip(f"shared Guobu audit wrapper is not installed: {SHARED_WRAPPER}")


def wrapper_text():
    return WRAPPER.read_text(encoding="utf-8")


def compact(text):
    return "".join(text.split()).lower()


def run_plan(project_root, tasks_dir, *extra, env=None):
    command = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WRAPPER),
        "-ProjectRoot", str(project_root), "-TasksDir", str(tasks_dir),
        "-RunName", "integration_contract", "-PlanOnly",
    ]
    if "-PythonExe" not in extra:
        command.extend(["-PythonExe", str(PROJECT_PYTHON)])
    command.extend(extra)
    return subprocess.run(
        command,
        **SUBPROCESS_TEXT, capture_output=True, timeout=60, env=env,
    )


def copy_required_prompts(project):
    prompts = project / "prompts"
    prompts.mkdir(exist_ok=True)
    for name in (
        "sn_similar_char_review.txt",
        "sn_similar_char_review_v2.txt",
        "sn_label_authenticity_review.txt",
        "digital_activation_evidence_review.txt",
        "photo_auth_edge_mapping_review.txt",
    ):
        (prompts / name).write_bytes((PROJECT_ROOT / "prompts" / name).read_bytes())


def copy_required_runtime_modules(project):
    modules = project / "modules"
    modules.mkdir(exist_ok=True)
    for name in (
        "__init__.py",
        "address_checker.py",
        "audit_models.py",
        "audit_runner.py",
        "category_classifier.py",
        "code_extractor.py",
        "id_card_parser.py",
        "image_forensics.py",
        "image_role.py",
        "ocr_engine.py",
    ):
        (modules / name).write_bytes((PROJECT_ROOT / "modules" / name).read_bytes())


def make_stub_project(tmp_path, output_order_ids):
    project = tmp_path / "project"; tools = project / "tools"; tasks = project / "tasks"
    tools.mkdir(parents=True); tasks.mkdir()
    copy_required_prompts(project)
    copy_required_runtime_modules(project)
    (tasks / "one.json").write_text(json.dumps({"channel_order_no": "order-1"}), encoding="utf-8")
    for name in ("run_guobu_audit_batch.ps1", "select_guobu_tasks.py",
                 "guobu_audit_contract.py", "photo_authenticity_mainline.py",
                 "guobu_sn_policy_v2.py", "guobu_sn_barcode.py",
                 "black_edge_shadow_detector.py",
                 "compliance_candidate_rules.py"):
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
        "-TasksDir", str(tasks), "-RunName", run_name, "-PythonExe", str(PROJECT_PYTHON)],
        **SUBPROCESS_TEXT, capture_output=True, timeout=30, env=env)


def test_plan_exposes_only_business_report_generator(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.json").write_text("{}", encoding="utf-8")
    completed = run_plan(PROJECT_ROOT, tasks)
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["reportGenerator"] == str(PROJECT_ROOT / "tools/guobu_audit_report.py")
    assert "reportFormat" not in plan


def test_plan_exposes_reversible_sn_character_review_prompt_plugin(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.json").write_text("{}", encoding="utf-8")

    default = run_plan(PROJECT_ROOT, tasks)
    assert default.returncode == 0, default.stderr
    default_plan = json.loads(default.stdout)
    assert default_plan["snCharReview"] is False
    assert default_plan["snCharReviewMode"] == "off"

    env_v2 = os.environ.copy()
    env_v2["SN_CHAR_REVIEW_MODE"] = "v2"
    explicit_switch_required = run_plan(PROJECT_ROOT, tasks, env=env_v2)
    assert explicit_switch_required.returncode == 0, explicit_switch_required.stderr
    assert json.loads(explicit_switch_required.stdout)["snCharReviewMode"] == "off"

    enabled = run_plan(PROJECT_ROOT, tasks, "-EnableSnCharReview")
    assert enabled.returncode == 0, enabled.stderr
    enabled_plan = json.loads(enabled.stdout)
    assert enabled_plan["snCharReview"] is True
    assert enabled_plan["snCharReviewMode"] == "on"

    enabled_v2 = run_plan(PROJECT_ROOT, tasks, "-EnableSnCharReviewV2")
    assert enabled_v2.returncode == 0, enabled_v2.stderr
    enabled_v2_plan = json.loads(enabled_v2.stdout)
    assert enabled_v2_plan["snCharReview"] is True
    assert enabled_v2_plan["snCharReviewMode"] == "v2"
    assert enabled_v2_plan["runManifest"]["sn_char_review_mode"] == "v2"

    conflict = run_plan(
        PROJECT_ROOT,
        tasks,
        "-EnableSnCharReview",
        "-EnableSnCharReviewV2",
    )
    assert conflict.returncode != 0
    assert "cannot be enabled together" in conflict.stderr

    dense = compact(wrapper_text())
    assert 'if($enablesncharreview-and$enablesncharreviewv2)' in dense
    assert 'if($enablesncharreviewv2){"v2"}elseif($enablesncharreview){"on"}else{"off"}' in dense
    assert '"--sn-char-review-mode",$sncharreviewmode' in dense


def test_plan_exposes_reversible_photo_auth_edge_mapping_prompt_plugin(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.json").write_text("{}", encoding="utf-8")

    default = run_plan(PROJECT_ROOT, tasks)
    assert default.returncode == 0, default.stderr
    default_plan = json.loads(default.stdout)
    assert default_plan["photoAuthEdgeMapping"] is False
    assert default_plan["runManifest"]["photo_auth_edge_mapping_mode"] == "off"
    assert "photo_auth_edge_mapping_review.txt" not in default_plan["runManifest"]["prompt_sha256"]
    assert "tools/black_edge_shadow_detector.py" not in default_plan["runManifest"]["runtime_sha256"]

    enabled = run_plan(PROJECT_ROOT, tasks, "-EnablePhotoAuthEdgeMapping")
    assert enabled.returncode == 0, enabled.stderr
    enabled_plan = json.loads(enabled.stdout)
    assert enabled_plan["photoAuthEdgeMapping"] is True
    assert enabled_plan["runManifest"]["photo_auth_edge_mapping_mode"] == "on"
    assert "photo_auth_edge_mapping_review.txt" in enabled_plan["runManifest"]["prompt_sha256"]
    assert "tools/black_edge_shadow_detector.py" in enabled_plan["runManifest"]["runtime_sha256"]

    source = wrapper_text()
    assert '"--photo-auth-edge-mapping-mode", $photoAuthEdgeMappingMode' in source


def test_photo_auth_edge_mapping_rejects_disabled_authenticity_mode(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.json").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["PHOTO_AUTHENTICITY_MODE"] = "off"

    completed = run_plan(PROJECT_ROOT, tasks, "-EnablePhotoAuthEdgeMapping", env=env)

    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["photoAuthenticityMode"] == "off"
    assert plan["photoAuthEdgeMapping"] is False


def test_photo_auth_edge_mapping_prompt_file_is_not_required_while_plugin_is_off(tmp_path):
    project, tasks = make_stub_project(tmp_path, ["order-1"])
    (project / "prompts/photo_auth_edge_mapping_review.txt").unlink()
    env = os.environ.copy()
    env.pop("PHOTO_AUTH_EDGE_MAPPING_MODE", None)

    disabled = run_plan(project, tasks, "-PythonExe", str(PROJECT_PYTHON), env=env)
    assert disabled.returncode == 0, disabled.stderr

    enabled = run_plan(
        project, tasks,
        "-PythonExe", str(PROJECT_PYTHON),
        "-EnablePhotoAuthEdgeMapping",
        env=env,
    )
    assert enabled.returncode != 0
    assert "photo_auth_edge_mapping_review.txt" in (enabled.stderr + enabled.stdout)


@pytest.mark.parametrize("switch", ["-EnableSnCharReview", "-EnableSnCharReviewV2"])
def test_plan_rejects_sn_character_review_plugins_in_sn_only_mode(tmp_path, switch):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.json").write_text("{}", encoding="utf-8")

    completed = run_plan(PROJECT_ROOT, tasks, "-Mode", "sn_only", switch)

    assert completed.returncode != 0
    assert "not applied in sn_only mode" in completed.stderr


def test_plan_preflights_project_python_and_exposes_run_manifest(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.json").write_text("{}", encoding="utf-8")

    completed = run_plan(PROJECT_ROOT, tasks, "-EnableSnCharReview")
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)

    assert Path(plan["pythonPath"]) == PROJECT_PYTHON
    assert plan["pythonVersion"] == ".".join(str(part) for part in sys.version_info[:3])
    assert plan["cv2Version"] == __import__("cv2").__version__
    manifest = plan["runManifest"]
    for field in (
        "model",
        "mode",
        "workers",
        "targeted_sn_review",
        "sn_char_review_mode",
        "sn_barcode_mode",
        "sn_label_auth_review_mode",
        "digital_activation_evidence_mode",
        "photo_authenticity_mode",
        "order_timeout_seconds",
        "git_commit",
        "python_path",
        "python_version",
        "cv2_version",
        "git_worktree_dirty",
        "runtime_sha256",
        "prompt_sha256",
    ):
        assert field in manifest
    assert manifest["sn_char_review_mode"] == "on"
    assert manifest["order_timeout_seconds"] == 60
    assert Path(manifest["python_path"]) == PROJECT_PYTHON
    assert set(manifest["prompt_sha256"]) == {
        "sn_similar_char_review.txt",
        "sn_similar_char_review_v2.txt",
        "sn_label_authenticity_review.txt",
        "digital_activation_evidence_review.txt",
    }
    assert all(len(value) == 64 for value in manifest["prompt_sha256"].values())
    for prompt_name in ("sn_similar_char_review.txt", "sn_similar_char_review_v2.txt"):
        assert manifest["prompt_sha256"][prompt_name] == hashlib.sha256(
            (PROJECT_ROOT / "prompts" / prompt_name).read_bytes()
        ).hexdigest()
    expected_dirty = bool(subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "status", "--porcelain", "--untracked-files=all"],
        **SUBPROCESS_TEXT, capture_output=True, timeout=10,
    ).stdout.strip())
    assert manifest["git_worktree_dirty"] is expected_dirty
    expected_runtime_sources = {
        "tools/run_guobu_audit_batch.ps1",
        "tools/run_guobu_model_audit_v2.py",
        "tools/guobu_sn_policy_v2.py",
        "tools/guobu_sn_barcode.py",
        "tools/guobu_audit_contract.py",
        "tools/guobu_audit_report.py",
        "tools/select_guobu_tasks.py",
        "tools/photo_authenticity_mainline.py",
        "modules/__init__.py",
        "modules/address_checker.py",
        "modules/audit_models.py",
        "modules/audit_runner.py",
        "modules/category_classifier.py",
        "modules/code_extractor.py",
        "modules/id_card_parser.py",
        "modules/image_forensics.py",
        "modules/image_role.py",
        "modules/ocr_engine.py",
    }
    assert expected_runtime_sources.issubset(set(manifest["runtime_sha256"]))
    assert manifest["sn_policy_version"] == "v2"
    assert manifest["sn_barcode_mode"] == "enforce"
    assert manifest["runtime_sha256"]["tools/run_guobu_audit_batch.ps1"] == hashlib.sha256(
        PROJECT_WRAPPER.read_bytes()
    ).hexdigest()
    assert all(len(value) == 64 for value in manifest["runtime_sha256"].values())


def test_wrapper_uses_explicit_utf8_streams_and_combined_json_decode():
    text = wrapper_text()
    dense = compact(text)

    assert "$outputencoding=" in dense
    assert "[console]::outputencoding" in dense
    assert "[system.io.file]::readalltext($combinedjson" in dense


def test_plan_exposes_reversible_sn_label_authenticity_plugin(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.json").write_text("{}", encoding="utf-8")

    default = run_plan(PROJECT_ROOT, tasks)
    assert default.returncode == 0, default.stderr
    assert json.loads(default.stdout)["snLabelAuthReview"] is False

    disabled_env = os.environ.copy()
    disabled_env["SN_LABEL_AUTH_REVIEW_MODE"] = "off"
    disabled = run_plan(PROJECT_ROOT, tasks, env=disabled_env)
    assert disabled.returncode == 0, disabled.stderr
    assert json.loads(disabled.stdout)["snLabelAuthReview"] is False

    enabled = run_plan(PROJECT_ROOT, tasks, "-EnableSnLabelAuthReview")
    assert enabled.returncode == 0, enabled.stderr
    assert json.loads(enabled.stdout)["snLabelAuthReview"] is True

    dense = compact(wrapper_text())
    assert 'elseif($enablesnlabelauthreview){"on"}' in dense
    assert '$snlabelauthreviewmode=if($photoauthenticitynewruleenabled-eq"false"){"off"}' in dense
    assert '"--sn-label-auth-review-mode",$snlabelauthreviewmode' in dense


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
    require_shared_wrapper()
    shared = compact(SHARED_WRAPPER.read_text(encoding="utf-8"))
    assert 'tools\\run_guobu_audit_batch.ps1' in shared
    assert '&$projectwrapper@forwardparams' in shared
    assert 'enablesncharreviewv2=$enablesncharreviewv2' in shared
    assert 'enablesnlabelauthreview=$enablesnlabelauthreview' in shared
    assert 'invoke-auditrun' not in shared
    assert "legacy" not in shared
    assert PROJECT_WRAPPER.is_file()


def test_shared_wrapper_forwards_sn_character_review_v2_to_project_plan(tmp_path):
    require_shared_wrapper()
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.json").write_text("{}", encoding="utf-8")

    completed = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SHARED_WRAPPER),
            "-ProjectRoot", str(PROJECT_ROOT), "-TasksDir", str(tasks),
            "-RunName", "shared_v2_contract", "-EnableSnCharReviewV2", "-PlanOnly",
        ],
        **SUBPROCESS_TEXT,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["snCharReview"] is True
    assert plan["snCharReviewMode"] == "v2"
    assert plan["runManifest"]["sn_char_review_mode"] == "v2"


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
        "--summary-json", str(summary)], **SUBPROCESS_TEXT, capture_output=True)
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


def test_reused_run_name_is_rejected_before_mutating_old_manifest(tmp_path):
    project, tasks = make_stub_project(tmp_path, output_order_ids=["order-1"])
    run_name = "existing_manifest_run"
    first_out = project / "reports" / "model_audit" / f"{run_name}_first"
    first_out.mkdir(parents=True)
    first_manifest = first_out / "run_manifest.json"
    sentinel = b'{"old":"manifest"}\n'
    first_manifest.write_bytes(sentinel)

    completed = invoke_offline_wrapper(project, tasks, run_name)

    assert completed.returncode != 0
    assert "RunName" in completed.stderr
    assert first_manifest.read_bytes() == sentinel
    assert not (project / "report-invoked.txt").exists()


@pytest.mark.parametrize("repeat", range(3))
def test_concurrent_same_run_name_atomically_reserves_single_runner(tmp_path, repeat):
    dense = compact(wrapper_text())
    assert "filemode]::createnew" in dense
    assert "fileshare]::none" in dense

    project, tasks = make_stub_project(tmp_path, output_order_ids=["order-1"])
    tools = project / "tools"
    run_name = f"atomic_concurrent_run_{repeat}"
    invocation_log = project / "runner-invocations.txt"
    (tools / "run_guobu_model_audit_v2.py").write_text(
        """import argparse, json, os, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--tasks-dir'); p.add_argument('--out-dir'); p.add_argument('--cache-dir')
p.add_argument('--model'); p.add_argument('--mode'); p.add_argument('--workers')
p.add_argument('--compliance-ruleset')
p.add_argument('--sn-char-review-mode')
p.add_argument('--sn-label-auth-review-mode')
p.add_argument('--sn-barcode-mode')
p.add_argument('--photo-auth-edge-mapping-mode')
p.add_argument('--digital-activation-evidence-mode')
p.add_argument('--photo-authenticity-mode')
p.add_argument('--photo-authenticity-new-rule-enabled')
p.add_argument('--photo-authenticity-local-tree-enabled')
p.add_argument('--photo-authenticity-local-tree-confirmation-enabled')
p.add_argument('--sn-policy-version')
p.add_argument('--no-targeted-sn-review', action='store_true')
a = p.parse_args()
root = Path(__file__).parents[1]
with (root / 'runner-invocations.txt').open('a', encoding='utf-8') as f:
    f.write(os.environ.get('ATOMIC_WORKER_ID', '?') + '\\n')
time.sleep(0.2)
out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
item = {'row': {'id': 'order-1', 'manual_reason': '', 'manual_reason_cn': '',
                'strategy': '', 'manual_flag': False}}
(out / 'result.jsonl').write_text(json.dumps(item) + '\\n', encoding='utf-8')
""", encoding="utf-8")

    launcher = tmp_path / "launch_atomic.ps1"
    launcher.write_text(
        """
param([string]$WorkerId)
$env:ATOMIC_WORKER_ID = $WorkerId
$ready = Join-Path $env:ATOMIC_BARRIER_DIR ($WorkerId + ".ready")
Set-Content -LiteralPath $ready -Value "ready" -Encoding ASCII
$deadline = (Get-Date).AddSeconds(10)
while ((Get-ChildItem -LiteralPath $env:ATOMIC_BARRIER_DIR -Filter "*.ready" | Measure-Object).Count -lt 2) {
  if ((Get-Date) -gt $deadline) { break }
  Start-Sleep -Milliseconds 20
}
& $env:ATOMIC_WRAPPER -ProjectRoot $env:ATOMIC_PROJECT -TasksDir $env:ATOMIC_TASKS -RunName $env:ATOMIC_RUN_NAME -PythonExe $env:ATOMIC_PYTHON
""",
        encoding="utf-8",
    )
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    env = os.environ.copy()
    env.update(
        VISION_API_BASE_URL="https://offline.invalid",
        VISION_API_KEY="dummy",
        ATOMIC_BARRIER_DIR=str(barrier),
        ATOMIC_WRAPPER=str(project / "tools/run_guobu_audit_batch.ps1"),
        ATOMIC_PROJECT=str(project),
        ATOMIC_TASKS=str(tasks),
        ATOMIC_RUN_NAME=run_name,
        ATOMIC_PYTHON=str(PROJECT_PYTHON),
    )

    processes = [
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher),
             "-WorkerId", worker_id],
            **SUBPROCESS_TEXT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        for worker_id in ("one", "two")
    ]
    completed = [process.communicate(timeout=60) + (process.returncode,) for process in processes]

    successes = [item for item in completed if item[2] == 0]
    failures = [item for item in completed if item[2] != 0]
    assert len(successes) == 1, completed
    assert len(failures) == 1, completed
    assert "RunName" in failures[0][1] or "already" in failures[0][1].lower()
    assert invocation_log.read_text(encoding="utf-8").splitlines() in (["one"], ["two"])
    first_manifest = project / "reports" / "model_audit" / f"{run_name}_first" / "run_manifest.json"
    assert first_manifest.is_file()


def test_runtime_hash_drift_is_rejected_before_network_retry(tmp_path):
    project = tmp_path / "project"
    tools = project / "tools"
    tasks = project / "tasks"
    tools.mkdir(parents=True)
    tasks.mkdir()
    copy_required_prompts(project)
    copy_required_runtime_modules(project)
    for name in ("run_guobu_audit_batch.ps1", "select_guobu_tasks.py",
                 "guobu_audit_contract.py", "photo_authenticity_mainline.py",
                 "guobu_sn_policy_v2.py", "guobu_sn_barcode.py",
                 "compliance_candidate_rules.py"):
        (tools / name).write_bytes((PROJECT_ROOT / "tools" / name).read_bytes())
    (tasks / "one.json").write_text(
        json.dumps({"channel_order_no": "order-1"}), encoding="utf-8")

    invocation_log = project / "model-invocations.txt"
    (tools / "run_guobu_model_audit_v2.py").write_text(
        """import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--tasks-dir'); p.add_argument('--out-dir'); p.add_argument('--cache-dir')
p.add_argument('--model'); p.add_argument('--mode'); p.add_argument('--workers')
p.add_argument('--compliance-ruleset')
p.add_argument('--sn-char-review-mode')
p.add_argument('--sn-label-auth-review-mode')
p.add_argument('--sn-barcode-mode')
p.add_argument('--photo-auth-edge-mapping-mode')
p.add_argument('--digital-activation-evidence-mode')
p.add_argument('--photo-authenticity-mode')
p.add_argument('--photo-authenticity-new-rule-enabled')
p.add_argument('--photo-authenticity-local-tree-enabled')
p.add_argument('--photo-authenticity-local-tree-confirmation-enabled')
p.add_argument('--sn-policy-version')
p.add_argument('--no-targeted-sn-review', action='store_true')
a = p.parse_args()
root = Path(__file__).parents[1]
with (root / 'model-invocations.txt').open('a', encoding='utf-8') as f:
    f.write(a.tasks_dir + '\\n')
report = Path(__file__).with_name('guobu_audit_report.py')
is_retry = 'network_retry_tasks' in Path(a.tasks_dir).name
if not is_retry:
    report.write_text(report.read_text(encoding='utf-8') + '\\n# runtime drift after first run\\n', encoding='utf-8')
out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
manual_reason = '' if is_retry else 'TimeoutError: request timed out'
item = {'row': {'id': 'order-1', 'manual_reason': manual_reason,
                'manual_reason_cn': manual_reason, 'strategy': '', 'manual_flag': bool(manual_reason)}}
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
Path(__file__).parents[1].joinpath('report-invoked.txt').write_text('yes')
Path(a.output_xlsx).write_bytes(b'xlsx-placeholder')
Path(a.output_json).write_text(json.dumps({'summary': {'stub': True}}), encoding='utf-8')
""", encoding="utf-8")

    run_name = "runtime_drift_retry_regression"
    env = os.environ.copy()
    env.update(VISION_API_BASE_URL="https://offline.invalid", VISION_API_KEY="dummy")
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WRAPPER),
         "-ProjectRoot", str(project), "-TasksDir", str(tasks), "-RunName", run_name,
         "-PythonExe", str(PROJECT_PYTHON)],
        **SUBPROCESS_TEXT, capture_output=True, timeout=30, env=env,
    )

    assert completed.returncode != 0
    assert "runtime_sha256" in completed.stderr
    assert invocation_log.read_text(encoding="utf-8").splitlines() == [str(tasks)]
    assert not (project / "report-invoked.txt").exists()
    second_manifest = project / "reports" / "model_audit" / f"{run_name}_network_rerun" / "run_manifest.json"
    assert not second_manifest.exists()


def test_stale_retry_task_directory_rejects_run_name_before_audit(tmp_path):
    project = tmp_path / "project"
    tools = project / "tools"
    tasks = project / "tasks"
    tools.mkdir(parents=True)
    copy_required_prompts(project)
    copy_required_runtime_modules(project)
    for name in ("run_guobu_audit_batch.ps1", "select_guobu_tasks.py",
                 "guobu_audit_contract.py", "photo_authenticity_mainline.py",
                 "guobu_sn_policy_v2.py", "guobu_sn_barcode.py",
                 "compliance_candidate_rules.py"):
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
p.add_argument('--compliance-ruleset')
p.add_argument('--sn-char-review-mode')
p.add_argument('--sn-label-auth-review-mode')
p.add_argument('--sn-barcode-mode')
p.add_argument('--photo-auth-edge-mapping-mode')
p.add_argument('--digital-activation-evidence-mode')
p.add_argument('--photo-authenticity-mode')
p.add_argument('--photo-authenticity-new-rule-enabled')
p.add_argument('--photo-authenticity-local-tree-enabled')
p.add_argument('--photo-authenticity-local-tree-confirmation-enabled')
p.add_argument('--sn-policy-version')
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
         "-ProjectRoot", str(project), "-TasksDir", str(tasks), "-RunName", run_name,
         "-PythonExe", str(PROJECT_PYTHON)],
        **SUBPROCESS_TEXT, capture_output=True, timeout=30, env=env,
    )

    assert completed.returncode != 0
    assert "RunName" in completed.stderr
    assert not invocation_log.exists()
    assert stale.exists()
    second_out = project / "reports" / "model_audit" / f"{run_name}_network_rerun"
    assert not second_out.exists()

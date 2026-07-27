# 第二轮独立对抗式审查报告

审查对象：`C:\Users\HUAWEI\Desktop\audit_robot` 当前工作树  
审查方式：只读核验 brief、三个任务报告、实施计划、代码、测试；唯一写入为本报告。  
测试复核：`$env:PYTHONDONTWRITEBYTECODE='1'; python -m pytest tests/test_guobu_v2_rules.py tests/test_guobu_audit_runtime_contract.py tests/test_guobu_audit_skill_report_integration.py tests/test_guobu_audit_report.py -q -p no:cacheprovider`  
实际结果：`415 passed in 37.67s`

## Findings

### Critical 1 - 60 秒不是绝对 per-order/model 预算；连接重试会额外消耗时间

证据：
- `tools/run_guobu_model_audit_v2.py:661-662` 定义 `MODEL_CONNECT_TIMEOUT_SEC = 5`、`MODEL_CONNECT_RETRIES = 1`，即最多两次连接尝试。
- `tools/run_guobu_model_audit_v2.py:1230-1232` 只在进入 `_post_chat_completion_json` 时把 `read_timeout_sec` 固定成 `stage_timeout_sec`。
- `tools/run_guobu_model_audit_v2.py:1239-1257` 在连接重试循环内，每次都使用 `min(MODEL_CONNECT_TIMEOUT_SEC, stage_timeout_sec)`，但没有按已经消耗的连接时间重新计算剩余 deadline。
- `tools/run_guobu_model_audit_v2.py:1246-1247` 第二次连接成功后又把 socket read timeout 设置为完整 `stage_timeout_sec`。
- `tests/test_guobu_v2_rules.py:541-588` 现有测试只证明“两次连接各 5 秒、第二次 read 仍为 60 秒”，没有证明两次连接加 read 被同一个绝对 deadline 约束。

可复现推理：
在 60 秒订单预算下，第一次连接超时消耗 5 秒；第二次连接仍可再消耗最多 5 秒；成功后 read 阶段仍可等待完整 60 秒。这样一次模型 stage 可超过 60 秒仍返回成功，外层不会转人工。这违反 brief 中“每单一个绝对 60 秒模型预算”和“5 秒 connect timeout 是 bounded sub-timeout”的要求。

影响：
- 家电、普通 3C、电脑、SN-only、hybrid/v2/fast 都经过 `_post_chat_completion_json`，所以这是跨模式运行时边界问题。
- 当前 415 个测试通过不能证明 60 秒绝对预算成立；相关测试反而固定了不扣减连接重试时间的行为。

### Critical 2 - 复用 RunName 时脚本会先写旧 run 的 manifest，然后才拒绝

证据：
- `tools/run_guobu_audit_batch.ps1:59-69` 根据 `RunName` 计算 `$firstOut`、`$combinedXlsx`、`$combinedJson`、`$firstManifest`。
- `tools/run_guobu_audit_batch.ps1:222-225` 先创建 `$firstOut`、`$firstCache`、`$reportRoot`、`$tempRoot`，并立即 `Write-Utf8Json -Path $firstManifest -Value $runManifest`。
- `tools/run_guobu_audit_batch.ps1:226-230` 直到写入之后才检查 combined 输出或 first-run JSONL 是否已存在并 throw。
- `tests/test_guobu_audit_skill_report_integration.py:196-207` 只做报表生成器路径和 `--overwrite` 字符串断言；没有复用 `RunName` 时旧目录不被写入的行为测试。

可复现推理：
如果旧 run 已存在 `reports/model_audit/<RunName>_first/run_manifest.json` 或 combined 输出，当前脚本会在拒绝前覆盖 first manifest。即使随后抛出 “Use a new RunName”，旧 run 已被突变。

影响：
- 明确不满足 final brief 要求：“reused RunName cannot mutate an old run before the script rejects it”。
- 旧 combined 报表本身在 `tools/run_guobu_audit_batch.ps1:226-230` 之前尚未被覆盖，但旧 run 目录内容已经被写入，仍属运行证据污染。

### Important 3 - Manifest 在 dirty worktree 下不真实，无法证明 retry 与 first run 使用同一代码状态

证据：
- `git status --short` 当前工作树存在大量 modified/untracked 文件，包括 `tools/run_guobu_model_audit_v2.py`、`tools/run_guobu_audit_batch.ps1`、`tools/guobu_audit_contract.py`、`tests/test_guobu_v2_rules.py`、`prompts/` 等。
- `tools/run_guobu_audit_batch.ps1:151-160` 只执行 `git rev-parse HEAD`。
- `tools/run_guobu_audit_batch.ps1:163-180` manifest 只写 `git_commit`、Python/cv2、prompt hash、模式参数等；没有 `worktree_dirty`、`git_status`、代码文件 hash 或脚本 hash。
- `tools/guobu_audit_contract.py:10-25` 兼容性字段包含 `git_commit` 和 `prompt_sha256`，不包含 dirty 状态或代码 hash。
- `tools/guobu_audit_contract.py:56-60` 只逐字段比较这些 manifest 字段。
- `tests/test_guobu_audit_skill_report_integration.py:119-144` 只断言 manifest 有 `git_commit` 和 prompt hash；没有 dirty worktree 真实性断言。

可复现推理：
在 dirty worktree 中，两次运行可以拥有相同 HEAD commit，但 `tools/run_guobu_model_audit_v2.py` 或 PowerShell wrapper 的未提交内容不同。当前 manifest 仍显示同一个 `git_commit`，兼容性校验也会通过，不能证明 first run 和 retry 使用同一代码状态。

影响：
- 不满足 final brief 要求：“manifest is truthful in a dirty Git worktree”。
- prompt hash 覆盖了 `prompts/`，但没有覆盖核心 Python/PowerShell 运行代码。

### Minor 4 - 家电例外的发票优先级缺少直接测试证据

证据：
- `tools/run_guobu_model_audit_v2.py:2398-2407` 把 `INVOICE_ORANGE_WARNING` 放在最高优先级。
- `tools/run_guobu_model_audit_v2.py:2444-2451` 家电无包装 gate 不应覆盖 `INVOICE_ORANGE_WARNING`。
- `tests/test_guobu_v2_rules.py:2916-2924` 家电 no-box gate 的高优先级覆盖测试只参数化了 `IMAGE_STRONG_RISK`、`DUPLICATE_IMAGE_EVIDENCE`、`PRODUCT_TYPE_MISMATCH`、`PRODUCT_PHOTO_INVALID`，没有覆盖 `INVOICE_ORANGE_WARNING`。

判断：
代码看起来保留了发票优先级，但测试证据不完整。考虑到 brief 明确要求家电仍 enforce invoice，这是一个应补的回归测试缺口，不是当前可确认的业务 bug。

## Scope Compliance

结论：部分合规，整体仍为 NEEDS WORK。

已核验通过的边界：
- 家电例外被限制在 `sn_already_verified and _is_home_appliance_decision(...)`：`tools/run_guobu_model_audit_v2.py:2452`。
- 普通 3C/电脑在 `activation_photo_ok=false` 下仍拦截：测试 `tests/test_guobu_v2_rules.py:2813-2858` 覆盖 `ordinary_3c` 和 `computer`。
- 家电缺包装/安装场景仍拦截：`tests/test_guobu_v2_rules.py:2723-2748`、`tests/test_guobu_v2_rules.py:2861-2887`。
- 家电例外不覆盖真实性、重复图、类型、商品图等更高优先级：代码 `tools/run_guobu_model_audit_v2.py:2471-2486`，测试 `tests/test_guobu_v2_rules.py:2916-2958`。
- `prompts/` 没被 `.gitignore` 忽略，顶层 runtime/output 路径被忽略：`.gitignore:15-18`，测试 `tests/test_guobu_audit_runtime_contract.py:69-80`。

不合规点：
- 60 秒预算不是绝对预算，见 Critical 1。
- 复用 RunName 会先写旧 run manifest，见 Critical 2。
- dirty worktree manifest 不真实，见 Important 3。

## Timeout / Batch Exit Check

结论：批处理“整批退出”风险在主 Python runner 层面基本被控制，但 60 秒绝对预算仍失败。

证据：
- `tools/run_guobu_model_audit_v2.py:3193-3240` 每个 task 通过 `audit_task_path` 调用对应 audit mode。
- `tools/run_guobu_model_audit_v2.py:3241-3258` 捕获每单异常，生成 `MODEL_UNCERTAIN`、`strategy = "error_to_manual"` 的结果行。
- `tools/run_guobu_model_audit_v2.py:3353-3364` 主循环继续收集 future 结果并写 partial JSONL。

限制：
- 单个 `audit_task_hybrid` 等函数仍可抛出 timeout；`tests/test_guobu_v2_rules.py:2027-2039` 甚至显式接受 `TimeoutError`。这不一定导致整批退出，因为外层 `audit_task_path` 会兜底，但直接调用这些函数时不是“返回 per-order result”。
- 更严重的是 Critical 1：连接重试可能使一个成功订单超过 60 秒，不一定触发 timeout result。

## Old Report / Output Check

结论：combined 报表文件本身有拒绝和原子覆盖保护，但 wrapper 的 RunName 写前拒绝顺序不安全。

证据：
- 报表生成器非 overwrite 时拒绝已有输出：`tools/guobu_audit_report.py:619-623`。
- overwrite 路径先写临时文件并校验，再 replace：`tools/guobu_audit_report.py:642-650`。
- 相关保护测试存在：`tests/test_guobu_audit_report.py:757-785`。
- wrapper 固定传 `--overwrite`：`tools/run_guobu_audit_batch.ps1:307-318`，依赖 wrapper 自己先拒绝复用 `RunName`；但该拒绝发生在 first manifest 写入之后：`tools/run_guobu_audit_batch.ps1:222-230`。

## Test Evidence Check

实际运行结果支持“415 affected tests passed”：

```text
415 passed in 37.67s
```

本轮没有发现任务报告中 GREEN 测试结果为虚构；但测试覆盖不足，不能支撑以下强声明：
- 不能证明 60 秒是绝对 per-order deadline。
- 不能证明复用 RunName 拒绝前不突变旧 run。
- 不能证明 dirty worktree manifest 真实、可复现、可阻止不兼容 retry 合并。

## Verdict

生产/部署就绪结论：FAILED / NEEDS WORK。

业务边界：家电激活例外本身看起来基本收敛，普通 3C/电脑边界有测试；但发票优先级缺少直接回归测试。

运行时边界：未达标。60 秒绝对预算、RunName 写前拒绝、dirty worktree manifest 是本轮必须修复的阻断项。

建议下一轮修复目标：
1. `_post_chat_completion_json` 接收 absolute deadline 或 remaining-budget callback；每次 connect/retry/read 前重新计算剩余时间，连接重试也必须消耗同一个 deadline。
2. PowerShell 在任何 `New-Item` 或 `Write-Utf8Json` 前检查 `$combinedXlsx`、`$combinedJson`、`$firstOut`、`$secondOut`、cache 目录是否表示旧 run，并为复用 `RunName` 添加行为测试。
3. Manifest 加入 dirty 状态和关键运行文件 hash，或明确标记 dirty 并在兼容性校验中处理；新增 dirty worktree 测试。
4. 补一个 `INVOICE_ORANGE_WARNING` 不被家电 fallback/no-box gate 覆盖的测试。

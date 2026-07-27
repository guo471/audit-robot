# 最终独立只读验收报告

验收范围：仅核验节流截止、原子 RunName 锁、原先已关闭项回归和业务边界。  
验收依据：`.superpowers/sdd/final-runtime-fix-report.md`、当前相关代码和聚焦测试。  
最终结论：**PASS**

## Findings

未发现本次限定范围内的 Critical 或 Important 阻断项。

## 1. 节流截止：PASS

- `tools/run_guobu_model_audit_v2.py:1194-1200` 让 `_wait_before_model_request` 接收绝对 stage deadline，并在进入等待前拒绝已过期预算。
- `tools/run_guobu_model_audit_v2.py:1203-1212` 在节流等待超过剩余预算时只 sleep 到剩余 deadline，随后抛出 `OrderBudgetExceeded`；未传 deadline 时仍执行原有完整间隔。
- `tools/run_guobu_model_audit_v2.py:1242` 建立 stage deadline，`tools/run_guobu_model_audit_v2.py:1290-1292` 启动硬定时器，`tools/run_guobu_model_audit_v2.py:1304-1305` 把同一 deadline 传入节流后再计算 connect 剩余时间。
- `tests/test_guobu_v2_rules.py:544-562` 以 0.05 秒 deadline 和 0.20 秒节流验证在 0.12 秒内抛 `OrderBudgetExceeded`，且不得启动连接。
- `tests/test_guobu_v2_rules.py:530-541` 验证无 deadline 时原有 3 秒请求间隔行为未变。

结论：上一轮“节流 sleep 拖过硬期限”的阻断已关闭。

## 2. 原子 RunName 锁：PASS

- `tools/run_guobu_audit_batch.ps1:71` 定义 run-specific reservation lock。
- `tools/run_guobu_audit_batch.ps1:269-290` 的预检包含 reservation、first/second/cache/retry/selection/combined 全部 run-specific 路径。
- `tools/run_guobu_audit_batch.ps1:292-301` 仅先创建共享父目录，随后用 `FileMode.CreateNew`、`FileAccess.ReadWrite`、`FileShare.None` 原子取得锁。
- `tools/run_guobu_audit_batch.ps1:309-323` 持锁后二次检查 run-specific 路径并创建 first 目录；只有 first 目录成为持久占位后才释放 reservation lock。
- `tools/run_guobu_audit_batch.ps1:324-325` 表明 first cache 和 manifest 仅在原子占位成功后写入。
- `tests/test_guobu_audit_skill_report_integration.py:359-438` 连续三轮同时启动两个同名进程，每轮断言恰好一个成功、一个失败，runner 仅调用一次。
- `tests/test_guobu_audit_skill_report_integration.py:342-356` 验证顺序复用不会改写旧 manifest sentinel。

结论：并发和顺序 RunName 复用均 fail closed，旧 run 不会在拒绝前被写入。

## 3. 原先已关闭项：PASS

- 渐进响应累计 deadline 仍由 `tools/run_guobu_model_audit_v2.py:1267-1288` 的分块 read/剩余时间检查和 `:1290-1292` 的 timer 共同约束；回归测试见 `tests/test_guobu_v2_rules.py:619-657`。
- connect retry 仍共享一个绝对 deadline，回归测试见 `tests/test_guobu_v2_rules.py:565-616`。
- `OrderBudgetExceeded` 仍进入 timeout retry，普通业务人工结果仍排除；实现见 `tools/guobu_audit_contract.py:6-9`、`:65-69`，测试见 `tests/test_guobu_audit_runtime_contract.py:92-119`。
- dirty/runtime manifest 漂移仍被拒绝，测试见 `tests/test_guobu_audit_runtime_contract.py:74-89`；retry 前运行时漂移阻断测试入口见 `tests/test_guobu_audit_skill_report_integration.py:441`。

结论：本次聚焦回归未发现既有关闭项重新打开。

## 4. 业务边界：PASS

- 家电激活 fallback 仍要求 SN 已验证且判定为家电，见 `tools/run_guobu_model_audit_v2.py:2527`。
- 普通 3C/电脑对无效激活证据仍返回 `ACTIVATION_PHOTO_INVALID`，见 `tools/run_guobu_model_audit_v2.py:2440-2452`。
- 发票、真实性、重复、类型、商品照、拆封优先级仍保留，见 `tools/run_guobu_model_audit_v2.py:2473-2481`、`:2514-2526`、`:2537-2559`。
- 家电 fallback、普通 3C/电脑拦截、缺少安装场景、no-box 隔离、更高优先级风险和发票保护测试分别见 `tests/test_guobu_v2_rules.py:2847`、`:2889`、`:2931`、`:2960`、`:2994`、`:3030`。

结论：未发现家电例外扩大到普通 3C、电脑、真实性、拆封、发票或重复规则。

## 聚焦验证

```text
25 passed in 17.13s
相关文件 git diff --check：exit 0（仅 LF-to-CRLF warning）
```

25 个实例覆盖节流 deadline、原有节流间隔、connect/retry、渐进 body、timeout 选择正反例、dirty/runtime manifest 漂移、三轮并发 RunName、旧 manifest 保护、retry 前 runtime 漂移及全部相关业务边界参数。按本次“可只跑聚焦测试”的指令，未重复执行四文件全量测试；最终判断不依赖修复报告中的历史全量结果。

## Verdict

- 节流截止：**PASS**
- 原子 RunName 锁：**PASS**
- 原先已关闭项：**PASS**
- 业务边界：**PASS**
- 最终：**PASS**


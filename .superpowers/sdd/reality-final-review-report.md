# 第三轮独立只读复审报告

审查范围：仅检查 `.superpowers/sdd/second-rereview-fix-report.md` 对应的剩余运行时阻断项。  
审查方式：源码/测试行号核对、定向测试、四个受影响测试文件及只读内联反例。  
最终结论：**NEEDS WORK**

## Finding

### Critical - 节流等待不能被 stage deadline 中断，绝对期限仍可被拖长

状态：**未关闭**。

证据：

- `.superpowers/sdd/second-rereview-fix-brief.md:9-12` 明确要求一个 wall-clock deadline 覆盖 throttle、connect、request、response headers 和 body。
- `tools/run_guobu_model_audit_v2.py:1281-1283` 在进入请求循环前启动 deadline timer，`tools/run_guobu_model_audit_v2.py:1295-1296` 在节流后检查剩余时间，因此节流消耗会被记账。
- 但 `tools/run_guobu_model_audit_v2.py:1198-1205` 的节流在锁内直接执行完整 `time.sleep(wait_sec)`，没有 deadline/event 参数，也没有按剩余预算缩短或中断 sleep。
- deadline 回调在 `tools/run_guobu_model_audit_v2.py:1249-1256` 只能设置 event 并关闭 `active_connection`；连接要到 `tools/run_guobu_model_audit_v2.py:1297-1298` 才创建。deadline 在 throttle 期间到达时没有活动连接可关闭，sleep 仍持续到原定结束。
- 现有节流测试 `tests/test_guobu_v2_rules.py:530-541` 只单测固定等待；渐进 I/O 测试在 `tests/test_guobu_v2_rules.py:623` 明确把 `_wait_before_model_request` 替换为空函数，没有覆盖 throttle 与 deadline 的组合。

只读内联反例设置 stage deadline 为 0.05 秒、节流等待为 0.20 秒，实际结果为：

```text
{"outcome":"OrderBudgetExceeded","deadline_sec":0.05,"throttle_sec":0.2,"elapsed_sec":0.203}
```

虽然最终异常类型正确，但函数在 deadline 后约 0.153 秒才返回；生产默认节流上限为 3 秒时，同类路径可把单单硬期限拖长接近 3 秒。因此“一个绝对 60 秒 deadline 覆盖 throttle”仍不成立。

## 五项验证

### 1. 渐进 I/O 累计 deadline：PASS

- `tools/run_guobu_model_audit_v2.py:1258-1279` 使用 `read1()` 分块读取并在块前后重算剩余时间。
- `tools/run_guobu_model_audit_v2.py:1281-1283` 启动硬定时器，`tools/run_guobu_model_audit_v2.py:1303-1315` 对 headers、body、JSON 返回及 deadline 触发的 I/O 异常执行检查/转换。
- `tools/run_guobu_model_audit_v2.py:1333-1336` 在 `finally` 中取消并回收 timer。
- 真实本地慢速分字节响应测试见 `tests/test_guobu_v2_rules.py:598-636`，在总传输超过 0.05 秒时断言 `OrderBudgetExceeded`。

结论：connect/request/headers/body 的累计 I/O 阻断已关闭；整体 deadline 仍被上方 throttle 问题阻断。

### 2. 节流等待：FAIL

节流被计入预算，但不能在 deadline 时及时中断。见唯一 Critical。

### 3. 超时重跑识别：PASS

- `tools/guobu_audit_contract.py:6-9` 同时包含 `orderbudgetexceeded` 和中文“超过每单60秒总期限”标记。
- `tools/guobu_audit_contract.py:65-69` 从 `_error`、人工原因、strategy 等实际结果字段识别网络/timeout 失败。
- `tools/select_guobu_tasks.py:15` 仅把 `network_failure()` 为真的首轮结果加入 timeout retry selection。
- 正向与普通业务人工反向测试见 `tests/test_guobu_audit_runtime_contract.py:92-119`。

结论：`OrderBudgetExceeded` 会进入网络重跑，普通业务人工结果不会被扩大选择。

### 4. 并发 RunName 原子占位：PASS

- `tools/run_guobu_audit_batch.ps1:268-285` 保留启动前路径扫描。
- `tools/run_guobu_audit_batch.ps1:287` 仅先创建共享父目录；`tools/run_guobu_audit_batch.ps1:288-292` 通过不带 `-Force` 的 first-run 目录创建执行原子占位。
- `tools/run_guobu_audit_batch.ps1:293-294` 仅在占位成功后创建 run-specific cache 并写 manifest。
- 强制两个进程同时到达占位点的测试见 `tests/test_guobu_audit_skill_report_integration.py:357-450`，断言恰好一个成功、一个失败，且模型 runner 只被调用一次。
- 已存在 RunName 的旧 manifest 字节保护测试见 `tests/test_guobu_audit_skill_report_integration.py:340-354`。

结论：顺序复用与并发同名竞态均已关闭。

### 5. 业务边界无变化：PASS

- 家电激活 fallback 仍要求 SN 已验证且判定为家电，见 `tools/run_guobu_model_audit_v2.py:2518`。
- 普通 3C/电脑的无效激活证据仍拦截，见 `tools/run_guobu_model_audit_v2.py:2431-2443`；回归测试见 `tests/test_guobu_v2_rules.py:2861-2906`。
- 发票、真实性、重复、类型、商品照、拆封优先级仍保留，见 `tools/run_guobu_model_audit_v2.py:2464-2473`、`:2510-2517`、`:2528-2550`。
- 家电缺少完整拆封/安装场景仍拦截，普通 3C/电脑不套用家电 no-box gate，见 `tests/test_guobu_v2_rules.py:2909-2961`；更高优先级风险与发票回归见 `:2964-3034`。

结论：未发现本轮运行时修复改变家电、普通 3C、电脑、真实性、拆封、发票或重复判定边界。

## 测试证据

```text
报告列出的 6 项定向测试：6 passed in 8.95s
四个受影响测试文件：424 passed in 42.32s
相关文件 git diff --check：exit 0（仅 LF-to-CRLF warning）
```

上述 GREEN 数量可复现，但现有测试缺少 throttle 与 deadline 组合路径，不能覆盖本报告的反例。

## Verdict

- 渐进 I/O 累计 deadline：**PASS**
- 节流等待：**FAIL**
- 超时重跑识别：**PASS**
- 并发 RunName 原子占位：**PASS**
- 业务边界：**PASS**
- 新增其他 Critical/Important：**无**
- 最终：**NEEDS WORK**


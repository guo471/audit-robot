# 第二轮独立现实复审报告

审查对象：`C:\Users\HUAWEI\Desktop\audit_robot` 当前工作树  
审查依据：`.superpowers/sdd/adversarial-fix-report.md`、原四项修复要求、当前相关代码与测试  
审查方式：除本报告外不改仓库文件；源码行号核对、定向测试、受影响测试集及只读内联反例  
最终结论：**NEEDS WORK**

## Findings

### Critical - 原 Critical 1 未关闭：read 仍不是累计绝对 deadline

状态：**OPEN / PARTIAL FIX**。

证据：

- `tools/run_guobu_model_audit_v2.py:1233-1239` 建立 stage deadline 并提供剩余时间计算。
- `tools/run_guobu_model_audit_v2.py:1252-1256` 在每次 connect 前重算 connect timeout，并在开始读取前把当时剩余时间一次性写入 socket timeout；connect/retry 部分已经修复。
- `tools/run_guobu_model_audit_v2.py:1258-1262` 随后直接执行 `getresponse()`、`response.read()` 并返回 JSON，read 完成后没有再次检查 deadline，也没有使用按累计墙钟时间截止的读取循环。
- socket timeout 约束的是一次阻塞操作的超时，不是整个分块响应的累计时长。只要服务端持续在每次 timeout 前送达少量数据，`response.read()` 可以累计超过剩余时间。
- `tests/test_guobu_v2_rules.py:541-592` 的新测试只让第一次 connect 消耗 5 秒、第二次 request 消耗 2 秒，然后让 `FakeHTTPResponse.read()` 立即返回；它只证明 read socket 被设置为 53 秒，没有证明累计 read 在 deadline 到达时会中止。

只读内联反例把 `read()` 的假时钟推进 61 秒后返回有效 JSON；当前函数输出：

```text
{"outcome":"returned_success_after_deadline","clock":1061.0,"socket_timeouts":[60.0],"result":{"ok":true}}
```

因此 `.superpowers/sdd/adversarial-fix-report.md:37-40` 关于 connect/retry/read 共用一个绝对 deadline 的声明不完整。一个订单仍可在 60 秒后成功返回，而不是按 `OrderBudgetExceeded` 转人工。

### Important - 新发现：并发复用 RunName 仍存在检查后写入竞态

状态：**NEW IMPORTANT**。已存在旧路径的顺序复用已修复，但“复用 RunName 零写入”还不是原子保证。

证据：

- `tools/run_guobu_audit_batch.ps1:268-283` 先用 `Test-Path` 扫描 run 专属路径。
- `tools/run_guobu_audit_batch.ps1:285-290` 检查返回后才用 `New-Item -Force` 创建目录并写 first manifest；检查与占位之间没有独占锁、原子目录创建失败判定或排他 sentinel。
- 可复现时序：进程 A 与 B 同时在 `:279` 看到路径不存在，随后 A 在 `:287-290` 创建并写入，B 也因 `-Force` 继续进入同一路径并覆盖/混写。
- `tests/test_guobu_audit_skill_report_integration.py:340-354` 只覆盖“路径在启动前已经存在”的顺序复用；当前测试文件没有并发同名启动用例。

这不否定原 Critical 2 的顺序场景修复，但在并发启动窗口内，旧 run/在途 run 仍可能被写入。

## 原四项关闭状态

| 原项 | 状态 | 证据与判断 |
|---|---|---|
| Critical 1：60 秒覆盖 connect/retry/read | **未关闭** | connect/retry 已按剩余时间重算（`tools/run_guobu_model_audit_v2.py:1247-1256`），累计 read 仍可越过 deadline 后成功返回（`:1258-1262`）；见上方反例。 |
| Critical 2：复用 RunName 拒绝前零写入 | **顺序场景已关闭；并发保证未关闭** | 所有已知 run 专属路径在 `tools/run_guobu_audit_batch.ps1:268-285` 检查，首次目录/manifest 写入在 `:287-290`；sentinel 行为测试见 `tests/test_guobu_audit_skill_report_integration.py:340-354`。并发竞态见新 Important。 |
| Important 3：dirty manifest 真实且 retry 前重算 | **已关闭** | runtime 列表见 `tools/run_guobu_audit_batch.ps1:145-162`；dirty 读取见 `:191-200`；manifest 字段见 `:203-223`；retry 在网络重跑前重新调用 `New-RunManifest`、先校验再写入见 `:358-376`；兼容字段和逐字段比较见 `tools/guobu_audit_contract.py:10-26`、`:52-62`。PlanOnly 真值/hash 测试见 `tests/test_guobu_audit_skill_report_integration.py:126-194`，运行时代码漂移阻断第二次模型调用见 `:357-425`。 |
| Minor 4：家电发票优先级直接测试 | **已关闭** | `tools/run_guobu_model_audit_v2.py:2411-2420` 将 `INVOICE_ORANGE_WARNING` 置于最高优先级；家电 fallback 仅清理 `ACTIVATION_PHOTO_INVALID`，见 `:2477-2481`；直接回归测试见 `tests/test_guobu_v2_rules.py:2965-2990`。 |

## 对抗式边界结论

### 家电边界：PASS

- 家电激活例外严格要求 `sn_already_verified` 且 `_is_home_appliance_decision(...)`，见 `tools/run_guobu_model_audit_v2.py:2465`。
- 普通 3C/电脑对无效激活证据仍返回 `ACTIVATION_PHOTO_INVALID`，见 `tools/run_guobu_model_audit_v2.py:2378-2390`；直接测试覆盖两类，见 `tests/test_guobu_v2_rules.py:2817-2862`。
- 发票、真实性、重复、类型、商品照、拆封优先级仍在 fallback 之前，见 `tools/run_guobu_model_audit_v2.py:2411-2420`、`:2457-2464`、`:2475-2481`。没有发现家电例外扩大到普通 3C、电脑、真实性或拆封规则。

### 60 秒与整批：PARTIAL

- 单单异常由 `tools/run_guobu_model_audit_v2.py:3224-3271` 捕获并转为 `MODEL_UNCERTAIN / error_to_manual`；主循环在 `:3366-3377` 继续收集 future 并写 partial JSONL。
- 精确抛出 `OrderBudgetExceeded` 的只读内联检查返回：`{"manual_reason_code":"MODEL_UNCERTAIN","strategy":"error_to_manual","error_type_recorded":true}`。因此 timeout 异常本身不会直接终止整批。
- 但 Critical 1 的慢速 read 可让 future 超过 60 秒继续等待，故“每单绝对 60 秒”仍不成立。

### 旧报表与 RunName：PARTIAL

- 启动前已存在的 first/second/cache/retry/selection/combined 路径均在任何仓库输出写入前检查，见 `tools/run_guobu_audit_batch.ps1:268-290`；顺序复用不会改写旧 manifest。
- 并发同名启动仍有检查后写入竞态，见新 Important，因此绝对“零写入”保证尚未完成。

### Dirty manifest 与 retry：PASS

- 当前 dirty 工作树被记录为 `git_worktree_dirty=true`；固定 runtime/prompt hash 均进入 manifest。
- retry 候选 manifest 在网络重跑前重新计算，并在创建 second 输出目录和写 second manifest 前完成兼容校验，见 `tools/run_guobu_audit_batch.ps1:358-376`。

## 测试证据核验

 fresh verification：

```text
7 项报告列明的定向测试：7 passed in 10.35s
四个受影响测试文件：420 passed in 46.79s
git diff --check（相关修复文件）：exit 0
```

修复报告中的当前 GREEN 数量可复现，不是虚构。历史 RED 需要回退代码才能重新执行，本轮受“只读不改”约束未重演，也未把历史 RED 文本单独作为通过依据。420 项通过不能覆盖上方慢速累计 read 反例。

## Scope / Code Verdict

- 新 Critical：**无**；但原 Critical 1 仍未关闭。
- 新 Important：**1 项**，并发 RunName 原子占位缺失。
- Scope verdict：**业务边界 PASS**。没有发现家电例外扩展到普通 3C、电脑、真实性、拆封、发票或其他禁止修改的判定边界。
- Code verdict：**NEEDS WORK**。在绝对 read deadline 和并发 RunName 排他性完成前，不能给出 PASS。


# AI 审核机器人国补影子试跑实施计划（中文审阅版）

> 对应执行版计划：`docs/superpowers/plans/2026-06-28-ai-audit-shadow-run-plan.md`  
> 对应需求文档：`docs/superpowers/specs/2026-06-28-ai-audit-final-requirements.md`  
> 本文用途：给你和技术人员审阅整体实施路线。真正写代码时，以英文执行版里的具体代码、测试和命令为准。

## 1. 这份计划要实现什么

先做一个“国补订单影子试跑系统”。

它的作用是：

1. 把你从审核后台采集到的订单字段和图片整理成本地任务。
2. 调用国内多模态模型识别商品图、SN 图、激活图等证据。
3. 用本地规则做最终判断。
4. 输出一张判断表：
   - 渠道订单号
   - 是否转人工
   - 转人工原因

首期不做这些事：

- 不自动点击通过。
- 不自动驳回。
- 不改后台订单状态。
- 不上传身份证图片。
- 不处理非发券复杂活动。

首期的目标不是“直接上线替代人工”，而是先每天跑 100-200 单，累计 200-500 单，验证模型识别、规则判断、成本、耗时和误判风险。

## 2. 总体技术路线

首期流程：

```text
审核后台网页
  ↓
手动采集 / Playwright / 影刀采集
  ↓
生成本地任务 JSON
  ↓
写入 SQLite 本地任务队列
  ↓
调用多模态模型
  ↓
校验模型 JSON 输出
  ↓
本地规则裁决
  ↓
导出 CSV 判断表
```

正式接入后的流程：

```text
业务后台写入任务表
  ↓
AI Worker 领取任务
  ↓
下载或读取图片
  ↓
调用模型
  ↓
本地规则裁决
  ↓
写回 AI 判断和转人工原因
  ↓
后台前端展示给审核员
```

## 3. 为什么要这样拆

目前你的最大限制不是 AI 模型，而是：

- 后台暂时不能配合改接口。
- 后台没有导出功能。
- 只能从网页详情页一单一单采样。
- 多模态模型供应商还没实测。
- Prompt 和模型输出稳定性还没验证。

所以计划先做本地可跑的最小闭环：

1. 不等后台技术。
2. 不碰真实订单状态。
3. 先用 JSON/SQLite/CSV 跑起来。
4. 等影子试跑证明有效，再推动后台正式接入。

这能避免一开始就做一个很大的系统，最后卡在后台接口、页面采集或模型稳定性上。

## 4. 文件结构规划

计划会新增这些核心文件。

### 4.1 数据结构

文件：

```text
modules/shadow_models.py
```

作用：

- 定义影子试跑任务结构。
- 定义图片结构。
- 定义模型识别结果结构。
- 定义最终判断结果结构。
- 定义标准转人工原因码。

主要概念：

```text
ShadowTask       一条待审核影子任务
ShadowImage      一张订单图片
ModelEvidence    模型返回的证据
DecisionResult   本地裁决后的结果
```

### 4.2 本地任务队列

文件：

```text
modules/shadow_queue.py
```

作用：

- 创建 SQLite 数据库。
- 导入任务。
- 领取待处理任务。
- 写回处理结果。
- 查询已完成结果。

首期不依赖后台数据库，先用 SQLite。

### 4.3 模型 JSON 校验

文件：

```text
modules/model_schema.py
```

作用：

- 检查模型是否返回合法 JSON。
- 检查字段是否完整。
- 检查 SN 置信度是否合法。
- 检查图片风险等级是否合法。

如果模型返回自然语言，或者缺字段，系统不能自动通过，必须转人工。

### 4.4 本地裁决器

文件：

```text
modules/shadow_decider.py
```

作用：

- 根据模型证据和本地规则判断：
  - `pass_candidate`：影子判断为可自动通过候选。
  - `manual`：疑单，转人工。

裁决器是保命模块。模型说“可以通过”也不能直接通过，必须经过裁决器。

### 4.5 判断表输出

文件：

```text
modules/shadow_report.py
```

作用：

- 生成 CSV 判断表。
- 至少包含：
  - 渠道订单号
  - 是否转人工
  - 转人工原因

建议同时包含：

- AI 判断
- 系统 SN
- 模型识别 SN
- 图片风险
- 模型名称
- Prompt 版本
- 耗时
- 单单成本

### 4.6 模型适配器

文件：

```text
modules/vision_adapters.py
```

作用：

- 把不同模型供应商封装成统一接口。
- 支持本地假数据测试。
- 支持 OpenAI-compatible API 形式的视觉模型。

为什么需要适配器：

- 通义、豆包、智谱等模型的接口可能不同。
- 但后面的裁决器不应该关心模型是谁。
- 所有模型都要转成统一 JSON 再裁决。

### 4.7 命令行工具

文件：

```text
tools/import_shadow_tasks.py
tools/run_shadow_audit.py
tools/export_shadow_report.py
```

作用：

- 导入样本任务。
- 批量调用模型并裁决。
- 导出判断表。

首期你可以用这三条命令完成一次完整试跑。

### 4.8 Prompt 文件

文件：

```text
prompts/guobu_v20260628.json
```

作用：

- 保存国补图片识别 Prompt。
- 记录 Prompt 版本。
- 避免把 Prompt 写死在代码里。

每次改 Prompt，都能知道是哪一版影响了结果。

### 4.9 浏览器采集器脚手架

文件：

```text
tools/collect_shadow_tasks_playwright.py
docs/collector-selector-guide.md
```

作用：

- 先提供 Playwright 采集器框架。
- 真实页面选择器需要你打开后台后确认。
- 采集器必须只读字段和下载图片，不能点通过或驳回。

### 4.10 后台正式接入合同

文件：

```text
docs/backend/ai_audit_task_contract.md
```

作用：

- 给后台技术看的正式接入说明。
- 包括任务表 SQL、状态流转、AI Worker 领单方式、结果写回格式。

## 5. 任务拆分

计划拆成 9 个任务，每个任务都可以单独测试。

## Task 1：影子试跑数据结构

目标：

先把系统里用到的数据结构定下来。

新增文件：

```text
modules/shadow_models.py
tests/test_shadow_models.py
```

实现内容：

- `ShadowTask`：一条订单任务。
- `ShadowImage`：一张图片。
- `ModelEvidence`：模型识别证据。
- `DecisionResult`：最终判断结果。
- `REASON_CODES`：标准原因码。

验收方式：

运行：

```powershell
pytest tests/test_shadow_models.py -v
```

通过标准：

- 能从需求文档里的任务 JSON 创建 `ShadowTask`。
- 能把 `DecisionResult` 转成判断表需要的字段。
- `是否转人工` 能输出中文“是/否”。

## Task 2：SQLite 本地任务队列

目标：

让任务可以本地排队、领取、完成和导出。

新增文件：

```text
modules/shadow_queue.py
tests/test_shadow_queue.py
```

实现内容：

- 创建 SQLite 表。
- 导入任务。
- 领取下一条待处理任务。
- 写回处理结果。
- 查询已完成任务。

任务状态：

```text
pending      待处理
processing   正在处理
done         已完成
failed       处理失败
```

验收方式：

运行：

```powershell
pytest tests/test_shadow_queue.py -v
```

通过标准：

- 任务能写入数据库。
- 任务能被领取。
- 处理结果能写回。
- 同一个渠道订单号不能重复导入。

## Task 3：模型 JSON 校验器

目标：

防止模型乱输出，尤其是输出自然语言、缺字段、瞎编格式。

新增文件：

```text
modules/model_schema.py
tests/test_model_schema.py
```

实现内容：

- 校验 `sn_candidates`。
- 校验 `image_risks`。
- 校验 `conflicts`。
- 校验 SN 置信度只能是：
  - `high`
  - `medium`
  - `low`
- 校验图片风险等级只能是：
  - `none`
  - `weak`
  - `strong`

验收方式：

运行：

```powershell
pytest tests/test_model_schema.py -v
```

通过标准：

- 合法 JSON 能解析。
- 自然语言输出会报错。
- 非法置信度会报错。
- 非法风险等级会报错。

## Task 4：本地保守裁决器

目标：

实现真正的“能不能作为自动通过候选”的判断逻辑。

新增文件：

```text
modules/shadow_decider.py
tests/test_shadow_decider.py
```

自动通过候选必须同时满足：

1. 采集状态正常。
2. 渠道订单号存在。
3. 系统 SN 存在。
4. 至少有一张商品/SN/激活相关图片。
5. 模型 JSON 合法。
6. 模型识别到高置信 SN。
7. 高置信 SN 与系统 SN 完全一致。
8. 没有冲突 SN。
9. 没有强图片风险。
10. 没有模型异常。

否则全部转人工。

验收方式：

运行：

```powershell
pytest tests/test_shadow_decider.py -v
```

通过标准：

- SN 高置信一致时输出 `pass_candidate`。
- 系统 SN 缺失时转人工。
- 模型没识别到 SN 时转人工。
- SN 低置信时转人工。
- SN 不一致时转人工。
- 图片强风险时转人工。
- 模型异常时转人工。

## Task 5：判断表 CSV 输出

目标：

生成你能直接看的判断表。

新增文件：

```text
modules/shadow_report.py
tests/test_shadow_report.py
```

判断表最小列：

```text
渠道订单号
是否转人工
转人工原因
```

建议列：

```text
AI判断
系统SN
模型识别SN
图片风险
模型名称
模型版本
Prompt版本
耗时ms
单单成本元
```

验收方式：

运行：

```powershell
pytest tests/test_shadow_report.py -v
```

通过标准：

- CSV 能正常生成。
- 中文列名正确。
- `pass_candidate` 输出“是否转人工=否”。
- `manual` 输出“是否转人工=是”。

## Task 6：模型适配器和跑批命令

目标：

把前面几个模块串起来，形成一个本地可跑的影子试跑流程。

新增文件：

```text
modules/vision_adapters.py
tools/import_shadow_tasks.py
tools/run_shadow_audit.py
tools/export_shadow_report.py
tests/test_shadow_runner.py
```

实现内容：

1. `FixtureVisionAdapter`
   - 用本地假模型结果测试。
   - 不需要真实 API。

2. `OpenAICompatibleVisionAdapter`
   - 支持类似 OpenAI 格式的视觉模型 API。
   - 后续可接国内模型的兼容接口或中转接口。

3. 导入命令：

```powershell
python tools/import_shadow_tasks.py --db data/shadow.db --tasks-dir data/sample_tasks
```

4. 跑批命令：

```powershell
python tools/run_shadow_audit.py --db data/shadow.db --adapter fixture --fixtures-dir data/sample_fixtures --limit 10
```

5. 导出命令：

```powershell
python tools/export_shadow_report.py --db data/shadow.db --out data/reports/shadow_judgment.csv
```

验收方式：

运行：

```powershell
pytest tests/test_shadow_runner.py -v
```

通过标准：

- 能导入任务。
- 能用假模型结果跑批。
- 能写回 `pass_candidate`。
- 能导出判断表。

## Task 7：Prompt 和本地样例流程

目标：

准备一个不用真实 API 也能跑通的完整样例。

新增文件：

```text
prompts/guobu_v20260628.json
data/sample_tasks/20260628-000001.json
data/sample_fixtures/20260628-000001.json
```

修改文件：

```text
.gitignore
```

实现内容：

- 新增国补 Prompt。
- 新增一条样例任务。
- 新增一条假模型输出。
- 把运行时数据库、图片、报告目录加入 `.gitignore`。

验收方式：

运行：

```powershell
python tools/import_shadow_tasks.py --db data/shadow.db --tasks-dir data/sample_tasks
python tools/run_shadow_audit.py --db data/shadow.db --adapter fixture --fixtures-dir data/sample_fixtures --limit 10
python tools/export_shadow_report.py --db data/shadow.db --out data/reports/shadow_judgment.csv
```

预期输出：

```text
imported 1 task files
processed 1 task(s)
exported 1 result(s)
```

预期判断表：

```text
渠道订单号,是否转人工,转人工原因,AI判断,系统SN,模型识别SN,图片风险,模型名称,Prompt版本,耗时ms,单单成本元
QD202606280001,否,,pass_candidate,ABC123,ABC123,none,fixture,guobu_v20260628,12,0.0
```

## Task 8：Playwright 采集器脚手架

目标：

先做一个可配置的网页采集器框架，为后续真实采集做准备。

新增文件：

```text
tools/collect_shadow_tasks_playwright.py
docs/collector-selector-guide.md
tests/test_collector_selector_mapping.py
```

注意：

真实后台页面的 CSS 选择器现在还不知道，所以这一步不是直接写死采集逻辑，而是先做：

- 字段映射结构。
- 选择器配置校验。
- 任务 JSON 构建函数。
- 采集器使用说明。

采集器必须遵守：

- 只读字段。
- 只下载图片。
- 不点击通过。
- 不点击驳回。
- 不修改订单状态。
- 如果字段定位不稳定，立即停止。

验收方式：

运行：

```powershell
pytest tests/test_collector_selector_mapping.py -v
```

通过标准：

- 页面字段能映射成本地任务 JSON。
- 缺必填选择器时报错。

## Task 9：正式后台接入合同

目标：

给后台技术人员一份可以直接开发的接口/表结构说明。

新增文件：

```text
docs/backend/ai_audit_task_contract.md
```

内容包括：

- `ai_audit_task` MySQL 建表 SQL。
- 状态流转。
- AI Worker 如何领单。
- 如何写回结果。
- 前端应该展示哪些字段。
- 自动通过开关默认关闭。
- worker 掉线后任务如何回收。

正式状态：

```text
pending -> processing -> done
pending -> processing -> failed
processing -> pending  心跳超时回收
```

安全要求：

- AI 不自动驳回。
- 第一次正式接入只展示 AI 判断。
- 自动通过开关默认关闭。
- 5 分钟无心跳的任务释放回待处理。
- 异常必须写入错误信息，不能静默丢单。

## 6. 执行顺序

推荐顺序：

1. Task 1：先定数据结构。
2. Task 2：做 SQLite 队列。
3. Task 3：做模型 JSON 校验。
4. Task 4：做本地裁决器。
5. Task 5：做判断表。
6. Task 6：串成跑批命令。
7. Task 7：做本地假数据完整演示。
8. Task 8：做网页采集器框架。
9. Task 9：整理后台正式接入合同。

每完成一个 Task，都应该跑对应测试并提交一次。

## 7. 最终验证命令

全部实现后运行：

```powershell
pytest tests/test_shadow_models.py tests/test_shadow_queue.py tests/test_model_schema.py tests/test_shadow_decider.py tests/test_shadow_report.py tests/test_shadow_runner.py tests/test_collector_selector_mapping.py -v
python tools/import_shadow_tasks.py --db data/shadow.db --tasks-dir data/sample_tasks
python tools/run_shadow_audit.py --db data/shadow.db --adapter fixture --fixtures-dir data/sample_fixtures --limit 10
python tools/export_shadow_report.py --db data/shadow.db --out data/reports/shadow_judgment.csv
```

预期结果：

```text
所有 pytest 测试通过
imported 1 task files
processed 1 task(s)
exported 1 result(s)
```

## 8. 你审阅时重点看什么

你不需要看懂每一段代码。审阅时重点看这些问题：

1. 判断表字段是否够你用。
2. 转人工原因码是否符合你的审核习惯。
3. 首期只做国补是否正确。
4. 非发券和身份证是否确实应该后置。
5. 是否接受先用 SQLite、本地 JSON、CSV 跑起来。
6. 是否接受 Playwright 采集器先做脚手架，等真实页面选择器确认后再完善。
7. 后台正式接入表结构是否方便后续给技术沟通。

## 9. 关键风险提醒

这份计划仍然有几个风险：

1. 后台页面选择器未知，Playwright 采集器不能一次写死。
2. 图片 URL 是否能直接访问还没确认，首期默认下载本地再上传模型。
3. 三方 API 和正式模型可能不一致，所以必须记录模型版本和 Prompt 版本。
4. 模型可能幻觉，所以本地裁决器必须保守。
5. `pass_candidate` 只是影子通过候选，不是真正自动通过。
6. 数据外传合规需要正式确认。

## 10. 结论

这份计划的核心思路是：

先不要急着做完整后台系统，也不要急着自动通过订单。

先用本地影子试跑把这条链路跑通：

```text
样本任务 -> 模型识别 -> 本地裁决 -> 判断表 -> 人工抽查
```

等 200-500 单验证出：

- 哪些订单能稳定识别；
- 哪些原因会转人工；
- 模型成本是多少；
- 有没有误通过风险；
- 每天 100-200 单是否能跑得动；

再推动后台正式接入和自动通过开关。

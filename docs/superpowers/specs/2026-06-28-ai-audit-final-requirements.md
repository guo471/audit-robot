# 嘉惠多多 AI 审核机器人最终需求文档

> 版本：v1.0  
> 日期：2026-06-28  
> 首期目标：国补家电数码/3C 影子试跑，不改订单状态，不自动点击通过，不自动驳回  
> 最终目标：通过后台数据库/API 任务表接入，实现高置信订单自动通过，疑单转人工并给出原因  
> 核心原则：模型负责提取证据，本地规则负责裁决；宁可多转人工，不允许误通过

## 1. 背景和结论

当前已有项目具备本地 OCR、SN/IMEI 提取、图片风险检测、规则引擎、本地服务、脱敏和报表等基础能力。但首期无法依赖后台技术配合改数据库或接口，因此不能直接落地“后台任务表驱动”的最终形态。

最终方案采用三段式路线：

1. 首期：浏览器自动化或手动采集国补订单样本，调用国内多模态 API，生成判断表，不改订单状态。
2. 过渡期：稳定样本采集、Prompt、模型适配层和本地规则裁决器，形成每日 100-200 单影子试跑能力。
3. 正式期：后台技术配合新增任务表/API，中间层领取任务、调用模型、写回结果，前端展示 AI 判断和转人工原因。

旧方案的本地 OCR/规则能力保留为兜底，不作为国补首期主方案。原因是 SN 小字、反光、包装标签、图片角度会长期影响 OCR 准确率，训练本地 OCR 的样本标注成本较高，且不能从根本上解决图像理解问题。

首期推荐主路线：国补订单商品/SN 图片使用国内多模态模型识别，身份证图片不上传云端。非发券订单后置处理，因为不同活动规则和上传图片差异较大，且部分订单涉及身份证。

## 2. 业务范围

### 2.1 首期纳入范围

首期只做国补家电数码/3C 订单影子试跑。

纳入条件：

- 订单来自国补家电数码/3C 审核场景。
- 页面能读取渠道订单号、商品信息、系统 SN 和图片信息。
- 图片主要是商品照片、拆封照片、SN 码采集照片、激活照片等，不包含身份证照片。
- 只生成判断表，不点击通过，不点击驳回，不修改订单状态。
- 每天采集并处理 100-200 单，累计先跑 200-500 单形成第一轮评估。

首期输出判断：

- `是否转人工=否`：AI 影子判断为完全一致，理论上可自动通过。
- `是否转人工=是`：疑单，需要人工复核。

### 2.2 首期不纳入范围

以下场景首期不自动处理：

- 非发券订单中的身份证图片云端识别。
- 自动驳回。
- 自动点击通过。
- 汽车审核。
- 发票、能效、水效等复杂字段的最终合规判断。
- 需要业务解释的家电地址灰区。
- 页面字段缺失、图片无法下载、图片角色不明、模型异常的订单。

### 2.3 后续纳入范围

后续可扩展：

- 非发券 3C：身份证姓名和有效期由本地 OCR 或业务规则调整后处理。
- 后台数据库任务表接入。
- 前端展示 AI 识别原因。
- 多模型横评与模型切换。
- 条码/二维码辅助识别。

## 3. 核心原则

1. 首期只做影子判断，不改变真实订单状态。
2. 首期不自动驳回，任何不一致、不确定、异常、超时都转人工。
3. 模型只负责提取证据，不负责最终放行。
4. 本地规则裁决器负责最终判断，所有自动通过候选必须满足完整证据链。
5. 国补商品/SN 图片可以调用国内多模态 API；身份证图片不得上传云端。
6. 每次模型调用必须记录模型名称、模型版本、Prompt 版本、耗时、成本和输出摘要。
7. 判断表必须能被人工直接使用，也必须能支持后续复盘和调参。
8. 任何采集失败、模型 JSON 异常、字段缺失，默认转人工。
9. 不追求首期 90% 自动通过率，先验证误通过风险、成本、耗时和常见转人工原因。

## 4. 总体架构

```text
首期影子试跑

审核后台网页
  |
  | 手动采集 / Playwright / 影刀
  v
本地样本采集器
  |
  | 字段 JSON + 本地图片
  v
SQLite 本地任务队列
  |
  | pending -> processing
  v
多模态模型适配层
  |
  | 统一 JSON 证据
  v
本地规则裁决器
  |
  | pass_candidate / manual
  v
判断表 Excel/CSV
```

```text
正式后台接入

业务后台
  |
  | 写入 ai_audit_task
  v
数据库任务表
  |
  | AI Worker 原子领单
  v
中间层服务
  |
  | 下载图片 / 调用模型 / 本地裁决
  v
写回 ai_decision、manual_required、manual_reason、evidence_json
  |
  v
后台前端展示 AI 结果，人工复核疑单
```

## 5. 模块拆解

### 5.1 样本采集器

首期必须先解决数据采集。由于后台没有导出 Excel/CSV，只能一单一单打开详情页，推荐优先做 Playwright 采集器；如果 Playwright 登录、验证码或页面复杂度太高，则短期用影刀半自动采集。

职责：

1. 打开审核后台订单详情页。
2. 读取页面字段。
3. 识别图片标题和图片链接。
4. 下载图片到本地任务目录。
5. 生成本地任务 JSON。
6. 不点击通过，不点击驳回，不修改状态。

必须采集字段：

| 字段 | 说明 |
|---|---|
| `channel_order_no` | 渠道订单号，判断表主键 |
| `scene` | 场景，首期固定为 `guobu` 或从页面活动名识别 |
| `product_type` | 类型/品类 |
| `product_name` | 商品名称 |
| `brand` | 品牌 |
| `model` | 规格型号 |
| `system_sn` | 系统录入 SN |
| `imei1` / `imei2` | 页面存在时采集，不作为首期硬条件 |
| `barcode` | 页面存在时采集 |
| `address` | 页面存在时采集，只做记录和后续扩展 |
| `image.title` | 图片标题 |
| `image.local_path` | 本地图片路径 |

本地任务 JSON：

```json
{
  "task_id": "20260628-000001",
  "channel_order_no": "渠道订单号",
  "scene": "guobu",
  "fields": {
    "product_type": "手机",
    "product_name": "商品名称",
    "brand": "品牌",
    "model": "规格型号",
    "system_sn": "系统录入SN",
    "imei1": "",
    "imei2": "",
    "barcode": "",
    "address": ""
  },
  "images": [
    {
      "image_id": "img_001",
      "title": "SN码采集/激活照片",
      "local_path": "data/images/20260628-000001/img_001.jpg"
    }
  ],
  "source": {
    "collector": "playwright",
    "collected_at": "2026-06-28T14:00:00+08:00"
  }
}
```

采集失败处理：

| 失败情况 | 处理 |
|---|---|
| 渠道订单号缺失 | 任务标记 `FIELD_MISSING`，转人工 |
| 系统 SN 缺失 | 任务标记 `FIELD_MISSING`，转人工 |
| 图片下载失败 | 任务标记 `IMAGE_DOWNLOAD_FAILED`，转人工 |
| 页面结构变更 | 采集器停止批量运行，避免采错字段 |
| 登录失效 | 停止批量运行，人工重新登录 |
| 同一订单重复采集 | 使用 `channel_order_no` 去重 |

### 5.2 本地任务队列

首期推荐 SQLite，避免一开始就依赖后台数据库。

建表 SQL：

```sql
CREATE TABLE audit_task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT UNIQUE NOT NULL,
  channel_order_no TEXT NOT NULL,
  scene TEXT NOT NULL,
  fields_json TEXT NOT NULL,
  images_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  collection_status TEXT NOT NULL DEFAULT 'ok',
  model_name TEXT,
  model_version TEXT,
  prompt_version TEXT,
  decision TEXT,
  manual_required INTEGER,
  manual_reason TEXT,
  evidence_json TEXT,
  cost_cny REAL,
  elapsed_ms INTEGER,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX uk_audit_task_channel_order_no
ON audit_task(channel_order_no);

CREATE INDEX idx_audit_task_status_created
ON audit_task(status, created_at);
```

状态流转：

```text
pending -> processing -> done
pending -> processing -> failed
failed -> pending   手动重跑
```

状态定义：

| 状态 | 含义 |
|---|---|
| `pending` | 已采集，待处理 |
| `processing` | 正在调用模型或裁决 |
| `done` | 已生成判断结果 |
| `failed` | 系统异常，需要人工重跑或检查 |

### 5.3 多模态模型适配层

模型适配层必须独立，避免将业务规则绑定到某个供应商。

首期供应商方向：

- 国内模型优先。
- 候选包括通义千问 VL、豆包视觉模型、智谱 GLM-V 等。
- 最终以 30-50 单小样本横评决定首个生产候选。

选型标准：

1. 自动通过候选不能错。
2. SN 识别准确率高。
3. 能稳定返回 JSON。
4. 调用延迟可接受。
5. 单单成本可记录。
6. 支持图片文件上传或可访问图片 URL。
7. API 稳定性和错误码清晰。

统一模型输入：

```json
{
  "task_id": "20260628-000001",
  "channel_order_no": "QD202606280001",
  "fields": {
    "product_type": "手机",
    "product_name": "商品名称",
    "brand": "Apple",
    "model": "iPhone",
    "system_sn": "ABC123"
  },
  "images": [
    {
      "image_id": "img_001",
      "title": "SN码采集照片",
      "file_path": "data/images/20260628-000001/img_001.jpg"
    }
  ]
}
```

统一模型输出：

```json
{
  "schema_version": "1.0",
  "sn_candidates": [
    {
      "image_id": "img_001",
      "value": "ABC123",
      "confidence": "high",
      "evidence_text": "图片中可见SN: ABC123"
    }
  ],
  "product_evidence": {
    "brand_seen": true,
    "model_seen": false,
    "barcode_seen": false,
    "package_or_product_seen": true,
    "activation_or_sn_label_seen": true
  },
  "image_risks": [
    {
      "image_id": "img_001",
      "risk": "none",
      "risk_level": "none",
      "reason": ""
    }
  ],
  "conflicts": [],
  "summary": "SN一致，未发现明显翻拍、截图、拼图或P图风险"
}
```

JSON 校验要求：

- 必须是合法 JSON。
- 必须包含 `sn_candidates`、`image_risks`、`conflicts`。
- `confidence` 只允许 `high`、`medium`、`low`。
- `risk_level` 只允许 `none`、`weak`、`strong`。
- 缺字段、自然语言回答、无法解析，都视为 `MODEL_JSON_INVALID`。

Prompt 管理：

- Prompt 必须独立保存为版本化文件，例如 `prompts/guobu_v20260628.json`。
- 每次调用记录 `prompt_version`。
- Prompt 修改后必须先跑小样本，不允许直接覆盖大批量任务。
- Prompt 不直接决定通过或转人工，只要求模型返回证据。

### 5.4 本地规则裁决器

裁决器是保命模块。模型说“可以通过”也不能直接通过，必须由裁决器判断。

首期自动通过候选条件：

1. `collection_status=ok`。
2. 渠道订单号存在。
3. 系统 SN 存在。
4. 至少有一张商品/SN/激活相关图片。
5. 模型 JSON 合法。
6. 模型提取到至少一个 `confidence=high` 的 SN。
7. 高置信 SN 与系统 SN 完全一致。
8. 没有任何高置信 SN 与系统 SN 冲突。
9. 没有 `risk_level=strong` 的图片风险。
10. 没有模型超时或接口错误。
11. 未超出单单处理时限。

只要任一条件不满足，输出转人工。

裁决输出：

```json
{
  "task_id": "20260628-000001",
  "channel_order_no": "QD202606280001",
  "decision": "pass_candidate",
  "manual_required": false,
  "manual_reason": "",
  "evidence": {
    "field_ok": true,
    "sn_found": true,
    "sn_match": true,
    "sn_conflict": false,
    "strong_image_risk": false,
    "model_json_valid": true,
    "model_timeout": false
  }
}
```

转人工输出：

```json
{
  "task_id": "20260628-000002",
  "channel_order_no": "QD202606280002",
  "decision": "manual",
  "manual_required": true,
  "manual_reason": "SN_NOT_FOUND",
  "evidence": {
    "field_ok": true,
    "sn_found": false,
    "sn_match": false,
    "strong_image_risk": false,
    "model_json_valid": true
  }
}
```

### 5.5 判断表格生成器

用户必须看到的最小列：

| 列名 | 说明 |
|---|---|
| 渠道订单号 | 主键 |
| 是否转人工 | 是/否 |
| 转人工原因 | 标准原因码或简短中文原因 |

建议增加列：

| 列名 | 说明 |
|---|---|
| AI判断 | `pass_candidate` / `manual` |
| 系统SN | 可选，便于抽查 |
| 模型识别SN | 可选，便于定位误识别 |
| 图片风险 | `none` / `weak` / `strong` |
| 模型名称 | 便于横评 |
| 模型版本 | 便于复盘 |
| Prompt版本 | 便于回滚 |
| 耗时ms | 性能统计 |
| 单单成本元 | 成本统计 |
| 处理时间 | 批次追踪 |

Excel/CSV 示例：

| 渠道订单号 | 是否转人工 | 转人工原因 | AI判断 | 系统SN | 模型识别SN | 图片风险 | 模型名称 | Prompt版本 | 耗时ms | 单单成本元 |
|---|---|---|---|---|---|---|---|---|---:|---:|
| 12345 | 否 |  | pass_candidate | ABC123 | ABC123 | none | qwen-vl | guobu_v20260628 | 3200 | 0.02 |
| 12346 | 是 | SN_NOT_FOUND | manual | XYZ789 |  | none | qwen-vl | guobu_v20260628 | 4100 | 0.02 |

### 5.6 原因码

原因码必须标准化，不能只写大段自然语言。

| 原因码 | 中文说明 | 是否疑单 |
|---|---|---|
| `FIELD_MISSING` | 页面关键字段缺失 | 是 |
| `SYSTEM_SN_MISSING` | 系统 SN 缺失 | 是 |
| `IMAGE_MISSING` | 商品/SN相关图片缺失 | 是 |
| `IMAGE_DOWNLOAD_FAILED` | 图片下载失败 | 是 |
| `MODEL_JSON_INVALID` | 模型输出格式异常 | 是 |
| `MODEL_TIMEOUT` | 模型调用超时 | 是 |
| `MODEL_ERROR` | 模型接口错误 | 是 |
| `SN_NOT_FOUND` | 模型未识别到 SN | 是 |
| `SN_LOW_CONFIDENCE` | SN 置信度不足 | 是 |
| `SN_MISMATCH` | SN 与系统不一致 | 是 |
| `SN_CONFLICT` | 多张图识别出冲突 SN | 是 |
| `IMAGE_STRONG_RISK` | 图片存在强截图/翻拍/P图/拼图风险 | 是 |
| `UNSUPPORTED_CATEGORY` | 品类暂不支持 | 是 |
| `COLLECTOR_ERROR` | 采集器异常 | 是 |
| `PASS_CANDIDATE` | 证据链完整，可作为自动通过候选 | 否 |

### 5.7 正式后台中间层

正式接入时，后台技术需要新增任务表或等价 API。

MySQL 建表示例：

```sql
CREATE TABLE ai_audit_task (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  channel_order_no VARCHAR(64) NOT NULL,
  scene VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  fields_json JSON NOT NULL,
  images_json JSON NOT NULL,
  ai_decision VARCHAR(32),
  manual_required BOOLEAN,
  manual_reason VARCHAR(255),
  evidence_json JSON,
  model_name VARCHAR(64),
  model_version VARCHAR(64),
  prompt_version VARCHAR(64),
  cost_cny DECIMAL(10,4),
  elapsed_ms INT,
  locked_by VARCHAR(64),
  locked_at DATETIME,
  heartbeat_at DATETIME,
  error_message VARCHAR(512),
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uk_channel_order_no (channel_order_no),
  INDEX idx_status_created (status, created_at),
  INDEX idx_locked_at (locked_at)
);
```

正式状态流转：

```text
pending -> processing -> done
pending -> processing -> failed
processing -> pending  超时回收
```

原子领单要求：

- 多个 worker 不能抢到同一单。
- worker 处理期间写入 `heartbeat_at`。
- 超过 5 分钟无心跳的 `processing` 任务释放回 `pending`。
- 任务失败必须写 `error_message`，不能静默丢单。

MySQL 8 推荐使用事务和 `FOR UPDATE SKIP LOCKED`：

```sql
START TRANSACTION;

SELECT id
FROM ai_audit_task
WHERE status = 'pending'
ORDER BY created_at ASC
LIMIT 1
FOR UPDATE SKIP LOCKED;

UPDATE ai_audit_task
SET status = 'processing',
    locked_by = 'worker-01',
    locked_at = NOW(),
    heartbeat_at = NOW(),
    updated_at = NOW()
WHERE id = :id;

COMMIT;
```

如果数据库不支持 `SKIP LOCKED`，需要由后端实现等价的原子更新逻辑。

## 6. 实施计划

### Phase 0：准备和确认，0.5-1 天

目标：确认首期采集路径、模型候选和样本目录结构。

任务：

1. 确认审核后台登录方式、验证码、是否能保持登录态。
2. 确认图片链接是否能直接访问；如果不能，采集器必须下载本地文件再上传模型。
3. 确认国补页面字段名称和图片标题。
4. 确认首批测试模型供应商和 API Key。
5. 建立本地目录：

```text
data/
  tasks/
  images/
  reports/
  logs/
prompts/
```

验收：

- 能手动整理 3-5 单任务 JSON。
- 能确认图片是否需要本地上传。
- 能确定第一版 Prompt 文件名和模型名称。

### Phase 1：模型小样本横评，1-2 天

目标：用 30-50 单验证模型识别能力和 JSON 稳定性。

任务：

1. 为 30-50 单准备字段和图片。
2. 对候选模型调用同一套统一 Prompt。
3. 保存每个模型原始输出和标准化 JSON。
4. 人工核对：
   - SN 是否识别正确。
   - 是否误判图片风险。
   - JSON 是否稳定。
   - 是否出现幻觉。
5. 选择首个主模型和备选模型。

验收：

- 每个模型都有横评表。
- 选出主模型。
- 形成第一版 `guobu_v20260628` Prompt。
- 明确哪些输出必须由本地规则拦截。

### Phase 2：SQLite 队列和裁决器，1-2 天

目标：让任务可以批量处理并生成判断表。

任务：

1. 创建 SQLite 表。
2. 实现任务导入。
3. 实现模型适配层。
4. 实现 JSON schema 校验。
5. 实现本地规则裁决器。
6. 实现原因码。
7. 实现 Excel/CSV 报表。

验收：

- 能批量处理 30-50 单。
- 输出判断表至少包含：渠道订单号、是否转人工、转人工原因。
- 所有异常都能落到标准原因码。
- 不出现程序异常导致任务丢失。

### Phase 3：采集器，2-5 天

目标：提高样本采集效率。

优先方案：Playwright。

任务：

1. 复用浏览器登录态或人工登录后接管页面。
2. 打开订单详情页。
3. 读取字段。
4. 下载图片。
5. 写入 SQLite。
6. 采集失败时停止批量运行并报警。

备选方案：影刀。

适用情况：

- Playwright 难以处理登录、验证码、控件或图片下载。
- 需要快速半自动跑通首期样本。

验收：

- 每天可采集 100-200 单。
- 采集器不修改订单状态。
- 采集数据人工抽查字段准确。
- 图片能对应到正确订单。

### Phase 4：影子试跑，3-7 天

目标：累计 200-500 单，观察真实表现。

任务：

1. 每天处理 100-200 单。
2. 生成判断表。
3. 人工抽查所有 `是否转人工=否` 的订单。
4. 统计：
   - 影子自动通过候选比例。
   - 转人工原因分布。
   - 模型 JSON 异常率。
   - SN 识别失败率。
   - 图片风险误报率。
   - 单单耗时。
   - 单单成本。
5. 每天最多调整一次 Prompt，避免结果不可复盘。

验收：

- 累计 200-500 单完整结果。
- 自动通过候选抽查无明显误通过。
- 能说明主要转人工原因。
- 能估算每日 API 成本。

### Phase 5：正式后台接入设计，2-5 天

目标：给后台技术提供可直接开发的任务表和接口。

任务：

1. 后台创建 `ai_audit_task` 或等价任务表。
2. 后台将新订单写入任务表。
3. 中间层 worker 原子领单。
4. 中间层写回 AI 结果。
5. 后台前端显示：
   - 是否转人工。
   - 转人工原因。
   - AI 识别 SN。
   - 图片风险提示。
6. 上线时先只显示，不自动通过。
7. 经业务确认后，再开放高置信自动通过开关。

验收：

- 后台能看到 AI 判断。
- 无卡单、漏单、重复处理。
- worker 掉线后任务可回收。
- 自动通过开关默认关闭。

## 7. 成本估算

### 7.1 成本口径

由于模型价格会变化，项目不写死供应商价格，而采用运行时统计：

```text
单单成本 = 输入 token 成本 + 输出 token 成本 + 图片处理成本
日成本 = 单单平均成本 * 当日处理单量
月成本 = 日均成本 * 30
```

每条任务必须记录：

- `model_name`
- `model_version`
- `prompt_version`
- `input_tokens`
- `output_tokens`
- `image_count`
- `cost_cny`

价格以供应商官方价格页为准：

- 阿里云百炼模型价格：https://help.aliyun.com/zh/model-studio/model-pricing
- 火山方舟/豆包产品与价格页：https://www.volcengine.com/product/ark
- 智谱 API 价格页：https://bigmodel.cn/pricing

### 7.2 首期预估

首期每天 100-200 单，按每单 3 张图估算。

如果单单成本为：

| 单单成本 | 100 单/日 | 200 单/日 | 30 天 |
|---:|---:|---:|---:|
| 0.01 元 | 1 元/日 | 2 元/日 | 30-60 元 |
| 0.03 元 | 3 元/日 | 6 元/日 | 90-180 元 |
| 0.10 元 | 10 元/日 | 20 元/日 | 300-600 元 |
| 0.30 元 | 30 元/日 | 60 元/日 | 900-1800 元 |

用户当前不设硬上限，但系统必须设置停用条件：

- 单日成本超过预警值时停止批量任务。
- 连续模型异常超过阈值时停止调用。
- 单单成本异常升高时记录并停止该模型。

### 7.3 开发成本

| 模块 | 工期估算 |
|---|---:|
| 手动样本 JSON 和 Prompt 小样本 | 1-2 天 |
| SQLite 队列、模型适配、裁决器、报表 | 2-4 天 |
| Playwright 采集器 | 2-5 天 |
| 影子试跑和调参 | 3-7 天 |
| 后台任务表和中间层正式接入 | 5-10 天 |
| 前端原因展示 | 2-5 天 |

首期可用影子试跑版本：约 5-10 个工作日。  
正式后台接入版本：需要后台技术配合后再估算，通常 2-4 周更现实。

## 8. 验收标准

### 8.1 首期影子试跑验收

必须满足：

- 不修改订单状态。
- 不自动点击通过。
- 不自动驳回。
- 每天可处理 100-200 单。
- 判断表包含渠道订单号、是否转人工、转人工原因。
- 所有转人工都有标准原因码。
- 所有模型调用可追踪模型和 Prompt 版本。
- 自动通过候选经人工抽查无明显误通过。
- 成本和耗时可统计。

建议指标：

| 指标 | 首期目标 |
|---|---:|
| 样本量 | 200-500 单 |
| 判断表生成成功率 | >= 95% |
| 模型 JSON 合法率 | >= 95% |
| 自动通过候选误通过 | 0 |
| 单单平均耗时 | 3-15 秒，视模型而定 |
| 每日处理量 | 100-200 单 |

### 8.2 正式接入验收

必须满足：

- 任务表无重复处理。
- worker 掉线任务可回收。
- 模型异常自动熔断。
- 前端能展示 AI 原因。
- 自动通过开关默认关闭。
- 开启自动通过前，至少完成连续多日影子试跑抽查。

## 9. 未解决隐患和技术盲区

### 9.1 样本采集效率仍是首期最大瓶颈

当前后台没有导出功能，只能从网页详情页采集。Playwright 是否能稳定读取字段、下载图片、绕过登录态和验证码，需要实测。如果采集器不稳定，首期每天 100-200 单会受影响。

处理策略：

- 先手动采 30-50 单验证模型，不等采集器完成。
- Playwright 和影刀并行评估，优先选择更快稳定的方式。
- 采集器遇到字段错位必须停止，不允许继续批量采错数据。

### 9.2 图片 URL 是否可被模型访问未确认

如果图片 URL 需要登录态，模型无法直接读取。首期应默认下载到本地，再由程序上传给模型。

处理策略：

- Phase 0 测试图片 URL 访问。
- 模型适配层同时支持 URL 和本地文件上传。
- 正式后台接入时由中间层处理图片转发，不让模型直接访问内部系统。

### 9.3 三方 API 与正式模型可能不一致

测试阶段使用三方 API，后续正式接入可能换供应商或模型版本。Prompt 效果、JSON 稳定性、识别准确率都可能变化。

处理策略：

- 不把规则写死在 Prompt。
- 模型输出统一转成标准 JSON。
- 本地裁决器不随模型变化。
- 每次记录模型和 Prompt 版本。
- 换模型必须重新跑 30-50 单横评。

### 9.4 多模态模型会幻觉

模型可能把模糊字符猜成系统 SN，也可能错误判断图片风险。

处理策略：

- 要求模型输出 `confidence`。
- 只有 `high` 且完全匹配系统 SN 才能作为通过候选。
- 多张图出现冲突 SN，一律转人工。
- 模型无法解释证据时转人工。
- 人工抽查所有首期通过候选。

### 9.5 自动通过候选不等于真实可通过

首期输出 `pass_candidate`，不是正式 `pass`。它只说明机器认为“完全一致”，仍需人工验证。

处理策略：

- 判断表用“是否转人工=否”表达候选通过。
- 正式点击通过必须等影子试跑验证后由业务开启开关。
- 发现一次误通过，立即收紧规则并回放历史样本。

### 9.6 非发券身份证规则存在业务调整空间

用户认为身份证上传意义不大，且被驳回情况少。但只要业务规则仍要求身份证，机器人不能跳过。身份证图片不应上传云端。

处理策略：

- 非发券后置。
- 如果业务取消身份证上传，非发券可改成商品/SN 证据链。
- 如果继续保留身份证，则姓名和有效期用本地 OCR 或转人工兜底。

### 9.7 “一致则通过，不一致则驳回”的业务逻辑不能直接自动化

虽然业务本质是一致性审核，但首期不允许自动驳回。模型识别到不一致，只能作为疑单原因，不能自动拒绝。

处理策略：

- 统一输出 `manual_required=true`。
- 原因码写 `SN_MISMATCH` 等。
- 后台未来可给人工高亮疑点，但不自动驳回。

### 9.8 家电地址、能效、发票等灰区未纳入首期

国补家电可能涉及地址粒度、能效、发票等问题。首期如果把这些纳入自动通过硬判断，会大幅增加误判风险。

处理策略：

- 首期只围绕商品/SN/图片强风险做通过候选。
- 地址、能效、发票先记录，不作为自动通过核心条件。
- 后续用报表分析是否值得加入规则。

### 9.9 数据安全和合规需要正式确认

商品图和 SN 图虽不含身份证，但仍可能包含订单、用户、商户或地址信息。三方 API 上传前需要确认公司或业务是否允许。

处理策略：

- 首期只上传国补商品相关图，不上传身份证。
- 日志不保存图片 URL、完整地址、手机号、身份证号。
- 正式上线前由业务或技术确认数据外传边界。
- 如政策不允许外传，必须改成本地 OCR/本地模型或人工辅助。

### 9.10 成本和速率限制需要实测

供应商价格、限流、图片计费方式会变化。不能只按宣传价格估算。

处理策略：

- 每单记录真实成本。
- 设置单日成本预警。
- 触发 429 或连续超时自动切备选模型或停机。
- 价格以官方价格页为准。

## 10. 建设性建议

1. 不要先追求后台正式接入。先用 30-50 单把模型、Prompt、输出 JSON、原因码跑稳。
2. 不要训练本地 OCR 作为首期主路线。除非已经有大量标注样本，否则投入产出比低。
3. 不要让模型直接决定通过。模型只能提供证据，本地规则必须保守裁决。
4. 不要把 Prompt 写成业务规则全集。业务规则应在裁决器中可测试、可回滚、可审计。
5. 不要一开始处理非发券所有活动。非发券活动差异大，身份证又涉及隐私，适合后置。
6. 判断表要从第一天就保留模型版本和 Prompt 版本，否则后续无法解释为什么某天结果变了。
7. 正式后台表结构要沿用首期 SQLite 字段，避免首期成果无法迁移。
8. 后台正式接入后也要先只展示 AI 判断，不要立即自动通过。

## 11. 最终交付清单

首期技术交付：

- `prompts/guobu_vYYYYMMDD.json`
- SQLite 本地任务表
- 样本任务 JSON 格式
- 模型适配层
- JSON schema 校验器
- 本地规则裁决器
- 原因码字典
- Excel/CSV 判断表生成器
- Playwright 或影刀采集流程
- 每日试跑报告

正式技术交付：

- `ai_audit_task` 后台任务表
- AI Worker 服务
- 模型供应商适配配置
- Prompt 版本管理
- 任务锁和心跳回收
- 熔断和成本预警
- 后台前端 AI 原因展示
- 自动通过开关

## 12. 下一步

建议立即执行：

1. 手动整理 30-50 单国补样本。
2. 选 2-3 个国内多模态模型做横评。
3. 固化统一 JSON schema。
4. 实现 SQLite 队列、裁决器和判断表。
5. 再开发 Playwright 或影刀采集器，把每日样本量提高到 100-200 单。

完成 200-500 单影子试跑且自动通过候选无明显误通过后，再推动后台技术配合正式任务表接入。

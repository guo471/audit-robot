# 新手迁移指南：从影刀闭环迁移到 Playwright 手脚层

> 适合对象：第一次接手本项目的新同学  
> 目标：在不改审核规则的前提下，把页面操作层从影刀逐步迁移到 Playwright  
> 原则：先观察、后点击；只自动通过，不自动驳回；不确定就人工

## 1. 先理解系统分工

本项目分成两层：

- 本机审核服务：运行在 `http://127.0.0.1:8765/audit`，负责全部业务判断。
- 手脚层：影刀或 Playwright，负责打开网页、读取字段、下载图片、调用本机服务、根据结果点击按钮。

迁移时不要重写审核规则。你只需要让 Playwright 发送和影刀一样的 JSON 请求，并按同样的响应规则操作页面。

## 2. 准备环境

在项目目录执行：

```powershell
cd C:\audit_robot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

设置本机 token：

```powershell
$env:AUDIT_SERVICE_TOKEN="换成一串只有本机知道的密钥"
```

启动审核服务：

```powershell
python audit_service.py
```

确认服务可用：

```powershell
curl http://127.0.0.1:8765/health
```

看到类似结果即可：

```json
{"status":"ok"}
```

## 3. 先用一条假数据理解接口

新开一个 PowerShell，执行：

```powershell
$headers = @{ "X-Audit-Token" = $env:AUDIT_SERVICE_TOKEN }
$body = @{
  jl_order_no = "JL-DEMO-001"
  channel_order_no = "CH-DEMO-001"
  scene_hint = "国补家电数码"
  fields = @{
    product_type = "3C"
    product_name = "示例手机"
    sn = "SN001234"
  }
  images = @()
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8765/audit" `
  -Headers $headers `
  -Body $body `
  -ContentType "application/json"
```

没有图片时通常会返回 `decision=manual`。这不是错误，说明服务能正常收请求并做安全降级。

## 4. Playwright 需要采集哪些数据

每一单都重新读取页面，不要复用上一单的数据。

必须采集：

- `jl_order_no`：嘉联订单号；如果页面没有，可先传空。
- `channel_order_no`：渠道订单号；当嘉联订单号为空时可作为兜底订单号。
- `scene_hint`：页面路径、活动名或队列名，例如 `国补家电数码`、`非发券审核`。
- `fields`：页面字段，包括姓名、商品类型、商品名称、品牌、型号、SN、IMEI、地址等。
- `images`：已下载到本机临时目录的图片列表，每项包含页面标题和本机路径。

也可以先传 `page_text`，由本机服务尝试解析字段：

```json
{
  "jl_order_no": "",
  "channel_order_no": "CH202605220001",
  "scene_hint": "国补家电数码",
  "page_text": "页面可见文本...",
  "fields": {},
  "images": []
}
```

如果 `page_text` 和 `fields` 都提供，`fields` 中的非空值优先。

## 5. Playwright 请求格式

Playwright 最终发送的请求应保持这个形状：

```json
{
  "jl_order_no": "JL202605220001",
  "channel_order_no": "CH202605220009",
  "scene_hint": "国补家电数码",
  "fields": {
    "name": "张三",
    "product_type": "3C",
    "product_name": "iPhone 15 Pro",
    "brand": "Apple",
    "model": "A3104",
    "sn": "F2LX12345678",
    "imei1": "356789012345678",
    "imei2": "",
    "address": "上海市浦东新区示例路100号"
  },
  "images": [
    {
      "title": "商品照片",
      "path": "C:\\audit_robot\\temp\\playwright\\JL202605220001\\product.jpg"
    },
    {
      "title": "SN码采集照片",
      "path": "C:\\audit_robot\\temp\\playwright\\JL202605220001\\sn.jpg"
    }
  ]
}
```

请求头必须带：

```text
X-Audit-Token: <本机密钥>
```

## 6. 响应后怎么点页面

本机服务返回：

- `decision=pass`：点击通过或批准。
- `decision=manual`：点击下一单，或按配置停在当前单让人工处理。
- `decision=error`：第一阶段建议暂停，排查后再决定是否跳过。

禁止做的事：

- 禁止自动驳回。
- 禁止把图片、身份证、订单数据上传到云端 OCR 或第三方系统。
- 禁止把图片 URL、OCR 原文、身份证号、手机号、完整地址写入日志。
- 禁止在服务超时后重复提交同一订单，避免并发审核同一单。

## 7. 建议迁移步骤

### 第一步：观察模式

Playwright 只做这些事：

1. 打开订单。
2. 采集字段和图片。
3. 调用本机服务。
4. 把 `jl_order_no`、`decision`、`manual_reason`、`elapsed_sec` 写入安全日志。
5. 不点击通过。

观察模式至少跑一小批真实单，人工核对 `decision=pass` 的候选单是否真的可以通过。

### 第二步：半自动模式

Playwright 调用服务后：

- `decision=pass` 时先停住或高亮提示人工确认。
- `decision=manual` 时直接下一单。
- 记录所有转人工原因。

这一阶段用于确认页面采集字段没有错位，图片下载顺序和标题没有错。

### 第三步：自动通过模式

只有当抽查确认没有误通过后，再开启：

- `decision=pass` 自动点击通过。
- `decision=manual` 下一单或停留人工。
- `decision=error` 暂停。

上线后每天抽查自动通过单，并统计转人工原因。

## 8. 验证清单

迁移完成前逐项确认：

- 服务地址是 `http://127.0.0.1:8765/audit`。
- Playwright 请求带有 `X-Audit-Token`。
- 每一单都重新读取页面字段。
- `fields` 中 SN、品类、姓名、地址没有串单。
- 图片已下载到本机临时目录，传给服务的是本机路径，不是网页 URL。
- 服务返回 `manual` 时不会点击通过。
- 服务异常或超时时不会自动驳回。
- 日志不包含图片 URL、OCR 原文、身份证号、手机号、完整地址。
- 临时图片在单笔或批次结束后删除。

## 9. 常见问题

### 服务返回 401

请求头里的 `X-Audit-Token` 和服务进程的 `AUDIT_SERVICE_TOKEN` 不一致。重新设置环境变量并重启服务。

### 服务返回 manual

这通常是正常保护行为。优先看 `manual_reason`，常见原因是图片为空、SN 为空、图片风险未通过、地址粒度不足、单单超时。

### 页面字段解析不准

优先让 Playwright 直接传结构化 `fields`。`page_text` 适合过渡期兜底，不适合长期依赖。

### 图片顺序不稳定

不要只依赖页面图片顺序。尽量同时传图片标题 `title`，让本机服务按标题判断角色。对当前实现来说，SN 采集或激活图片建议放在 `images` 列表最后，便于快路径优先检查。

### 想改审核规则

不要在 Playwright 脚本里改。规则应回到 `modules/audit_runner.py`、`modules/category_classifier.py`、`modules/image_forensics.py` 等本机模块中修改，并补测试。

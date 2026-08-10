# Linux 自动审核闭环部署说明

## 目标

在 Linux 服务器上常驻运行国补自动审核：每 10 分钟抓取 `status=0` 且 `machineExamineStatus=null` 的新订单，按单进入主线审核，按单回显 `machineApproval`。空转时没有新订单也必须正常返回，不报错，不中断定时任务。

## 运行口径

- 状态库：按月生成 `audit_state_YYYY_MM.sqlite`，默认目录 `data/audit_state`；待处理队列会跨所有月库扫描，避免月初漏掉上月未完成单。
- 去重主键：优先 `applyId`，没有 `applyId` 时才使用渠道订单号兜底。
- 队列状态：`NEW -> AUDITING -> AUDIT_DONE -> FEEDBACK_DONE`。
- 回显失败：最多 3 次，间隔 5 秒、30 秒；每次失败立即写入 `FEEDBACK_RETRY_PENDING` 和累计次数，进程重启后从剩余次数继续；仍失败则进入 `MANUAL_FEEDBACK_REQUIRED`，继续下一单。
- 待处理队列超过 5 单时，只做心跳抓取，不下载新订单图片、不入库新订单；本地已入库未完成订单继续恢复处理。
- 审核租约：订单进入 `AUDITING` 后默认 1 小时内不能被第二个进程重复认领；超过租约仍未完成时才允许恢复。
- 回显通过：`status=1`。
- 回显不通过：`status=2`，并带 UTF-8 中文 `refuseMessage`。

## 生产开关

启动脚本固定以下口径：

```bash
SN_POLICY_VERSION=v2
SN_BARCODE_MODE=enforce
DIGITAL_ACTIVATION_EVIDENCE_MODE=on
PHOTO_AUTHENTICITY_MODE=enforce
PHOTO_AUTHENTICITY_NEW_RULE_ENABLED=true
PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED=false
PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED=false
```

## 服务器依赖

建议 Python 3.11 及以上。安装项目依赖：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r photo_authenticity/requirements-runtime.txt
```

如果服务器不需要训练真实性模型，不安装 `photo_authenticity/requirements-train.txt`。

启动脚本会优先使用项目目录下的 `.venv/bin/python`；没有 `.venv` 时才回退到 `python3`。如果服务器有固定解释器，也可以在 `.env` 中设置 `PYTHON_BIN=/opt/audit_robot/.venv/bin/python`。

## 环境变量

在项目根目录创建 `.env`，只放服务器本地密钥，不提交 Git：

```bash
VISION_API_BASE_URL=你的模型接口地址
VISION_API_KEY=你的模型密钥
GUOBU_COLLECTOR_BASE_URL=https://approval.jhddsz.com
GUOBU_APPROVAL_BASE_URL=https://approval.jhddsz.com
GUOBU_AUTH_TOKEN=后台 Authorization
MACHINE_APPROVAL_AUTH_TOKEN=后台 Authorization
GUOBU_AUDIT_STATE_DIR=/var/lib/audit_robot/state
GUOBU_AUDIT_TEMP_DIR=/tmp/audit_robot_guobu
GUOBU_AUDIT_LEASE_SECONDS=3600
GUOBU_EXIT_NONZERO_ON_ERRORS=true
```

日志、SQLite、报告和异常栈不得打印真实密钥。

## 手工试跑

先只跑一轮：

```bash
bash tools/start_guobu_linux_auto_audit.sh --once
```

常驻运行，生产推荐使用这一种方式：

```bash
bash tools/start_guobu_linux_auto_audit.sh
```

空转验收：后台没有新订单时，输出中 `fetched_count=0`、`processed_count=0`，进程不报错。

故障验收：接口 HTTP 失败或后台业务状态失败不能伪装成空转，必须进入输出里的 `errors`，但本轮进程仍正常结束，下一轮继续执行。

如果接入 XXL-JOB 这类任务调度器，建议开启 `GUOBU_EXIT_NONZERO_ON_ERRORS=true`。开启后，空转仍返回 0；真实错误、单单异常或回显最终失败转人工时返回 2，调度器可以直接标记失败并报警。

## XXL-JOB 对接

XXL-JOB 推荐使用单轮模式，不使用常驻模式：

```bash
cd /opt/audit_robot
GUOBU_AUTO_AUDIT_ENV_FILE=/opt/audit_robot/.env \
GUOBU_EXIT_NONZERO_ON_ERRORS=true \
bash tools/start_guobu_linux_auto_audit.sh --once
```

调度建议：

- 调度周期：10 分钟一次，Quartz 表达式可用 `0 0/10 * * * ?`。
- 阻塞处理策略：单机串行，禁止并发重叠。
- 路由策略：单机部署时固定到一台机器。
- 失败重试：建议 0 或 1 次；回显失败代码内已有 3 次业务重试，调度器不要反复重跑同一轮。
- 超时时间：必须大于正常单轮审核耗时；建议先用试跑数据估算，再设置。

XXL-JOB 日志验收看输出 JSON：

```json
{
  "heartbeat_only": false,
  "pending_before": 0,
  "fetched_count": 0,
  "reserved_count": 0,
  "skipped_duplicate_count": 0,
  "processed_count": 0,
  "feedback_done_count": 0,
  "callback_failed_count": 0,
  "manual_feedback_required_count": 0,
  "errors": []
}
```

判断口径：

- 空转成功：`fetched_count=0`、`processed_count=0`、`errors=[]`。
- 有新单且成功：`fetched_count>0`、`processed_count>0`、`feedback_done_count=processed_count`、`errors=[]`。
- 重复单被跳过：`skipped_duplicate_count>0`，不是错误。
- 队列积压只心跳：`heartbeat_only=true`，并且不会新抓订单。
- 需要人工处理：`manual_feedback_required_count>0` 或 `callback_failed_count>0`，需要查 SQLite 的 `MANUAL_FEEDBACK_REQUIRED`。
- 系统故障：`errors` 非空；开启 `GUOBU_EXIT_NONZERO_ON_ERRORS=true` 时 XXL-JOB 应标红。

查询最近处理结果：

```bash
python - <<'PY'
from pathlib import Path
from datetime import datetime
import os, sqlite3

state_dir = Path(os.getenv("GUOBU_AUDIT_STATE_DIR", "/var/lib/audit_robot/state"))
db = state_dir / f"audit_state_{datetime.now():%Y_%m}.sqlite"
print("DB:", db)
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

print("\n状态汇总:")
for row in conn.execute("SELECT status, COUNT(*) AS count FROM orders GROUP BY status ORDER BY status"):
    print(row["status"], row["count"])

print("\n最近20单:")
for row in conn.execute("""
    SELECT apply_id, channel_order_no, status, retry_count, error_text, updated_at
    FROM orders
    ORDER BY updated_at DESC
    LIMIT 20
"""):
    print(dict(row))
PY
```

最终验收标准：

- XXL-JOB 连续 3 次空转任务成功，不报错。
- 手工放入 1 单待审核订单后，下一轮 `processed_count>=1`。
- 对应订单进入 `FEEDBACK_DONE`，后台不再出现在 `status=0` 且 `machineExamineStatus=null` 列表。
- 人工制造一次错误 token 后，XXL-JOB 标红，输出 `errors` 或 `callback_failed_count>0`；恢复 token 后下一轮可继续。
- SQLite 中无大量长期停留在 `AUDITING` 的订单；如有，确认是否未超过 `GUOBU_AUDIT_LEASE_SECONDS`。

## systemd 服务

示例文件 `/etc/systemd/system/guobu-auto-audit.service`：

```ini
[Unit]
Description=Guobu Auto Audit Loop
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/audit_robot
ExecStart=/bin/bash /opt/audit_robot/tools/start_guobu_linux_auto_audit.sh
Restart=always
RestartSec=10
Environment=GUOBU_AUTO_AUDIT_ENV_FILE=/opt/audit_robot/.env
Environment=PYTHON_BIN=/opt/audit_robot/.venv/bin/python

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable guobu-auto-audit
sudo systemctl start guobu-auto-audit
sudo journalctl -u guobu-auto-audit -f
```

## 上线风险

- `machineApproval` 成功后，后台必须把订单移出 `status=0` 且 `machineExamineStatus=null`；如果后台未改变状态，SQLite 会拦截本地已见订单，但仍建议上线后对账确认后台状态流转。
- 回显接口失败不能假装完成，只能本地标记 `MANUAL_FEEDBACK_REQUIRED` 并继续下一单。
- 心跳抓取必须只读，不能入库新订单；本地未完成队列仍可继续审核和回显。
- 不建议用 cron 同时启动多个 `--once` 进程；如必须使用外部定时器，必须确保上一轮未结束时不再启动下一轮。代码内置审核租约可防同单即时重复认领，但运维侧仍应避免重叠进程。
- 图片字段必须按 `商品照片`、`拆封照片`、`SN码采集 / 激活照片` 分组进入主线。
- 中文原因必须 UTF-8 回显，避免 `refuseMessage` 乱码。

# 国补审核主线 Linux 生产部署说明

## 交付内容

本包是国补审核主线生产包。部署人员需要配置 `.env` 后运行启动脚本，不需要修改业务代码。

当前生产口径：

- SN 规则：`SN_POLICY_VERSION=v2`
- 条码救回：`SN_BARCODE_MODE=enforce`
- 家电 SN 冲突救回：`SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE=true`
- 数码激活证据：`DIGITAL_ACTIVATION_EVIDENCE_MODE=on`
- 图片真实性：`PHOTO_AUTHENTICITY_MODE=enforce`
- 图片真实性新规则：`PHOTO_AUTHENTICITY_NEW_RULE_ENABLED=true`
- 图片真实性本地树：`PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED=false`
- 图片真实性本地树确认：`PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED=false`

## 最低配置

- Linux x86_64
- CPU：4 核最低，8 核推荐
- 内存：8GB 最低，16GB 推荐
- 磁盘：50GB 最低，100GB 推荐
- Python：3.11 及以上
- Node.js：18 及以上
- 网络：服务器必须能访问后台接口、图片 CDN、模型接口

## 安装依赖

在服务器上执行：

```bash
cd /opt/audit_robot
bash deploy/linux/install_dependencies.sh
```

如果服务器没有 sudo 权限，请让运维先安装系统依赖，再执行脚本里的 Python 虚拟环境部分。

## 配置环境变量

```bash
cd /opt/audit_robot
cp deploy/linux/.env.example .env
chmod 600 .env
vi .env
```

必须填写：

- `VISION_API_BASE_URL`：模型接口地址
- `VISION_API_KEY`：模型密钥
- `GUOBU_AUTH_TOKEN`：后台 Authorization
- `MACHINE_APPROVAL_AUTH_TOKEN`：后台 Authorization，可与 `GUOBU_AUTH_TOKEN` 相同

真实 `.env` 不得进入 Git、日志、报告或工单。

## 启动前验证

只做预检，不抓单、不审核、不回显：

```bash
cd /opt/audit_robot
bash deploy/linux/validate_deployment.sh
```

预检通过后，再跑一轮：

```bash
cd /opt/audit_robot
GUOBU_AUTO_AUDIT_ENV_FILE=/opt/audit_robot/.env bash tools/start_guobu_linux_auto_audit.sh --once
```

空转时允许 `fetched_count=0`、`processed_count=0`，并且必须返回成功。

## XXL-JOB 单轮模式

推荐生产使用 XXL-JOB 每 10 分钟调一次单轮模式：

```bash
cd /opt/audit_robot
GUOBU_AUTO_AUDIT_ENV_FILE=/opt/audit_robot/.env \
GUOBU_EXIT_NONZERO_ON_ERRORS=true \
bash tools/start_guobu_linux_auto_audit.sh --once
```

调度建议：

- 周期：10 分钟一次
- 阻塞策略：单机串行，禁止并发重叠
- 调度重试：0 或 1 次
- 超时：大于单轮真实审核最大耗时

## systemd 常驻模式

如不用 XXL-JOB，可使用 `deploy/linux/systemd/guobu-auto-audit.service`：

```bash
sudo cp deploy/linux/systemd/guobu-auto-audit.service /etc/systemd/system/guobu-auto-audit.service
sudo systemctl daemon-reload
sudo systemctl enable guobu-auto-audit
sudo systemctl start guobu-auto-audit
sudo journalctl -u guobu-auto-audit -f
```

## 状态库

状态库默认目录：

```text
/var/lib/audit_robot/state
```

按月生成：

```text
audit_state_YYYY_MM.sqlite
```

状态流转：

```text
NEW -> AUDITING -> AUDIT_DONE -> FEEDBACK_DONE
```

回显失败：

```text
FEEDBACK_RETRY_PENDING -> MANUAL_FEEDBACK_REQUIRED
```

回显失败最多重试 3 次；仍失败时本地标记人工处理，继续下一单，不能卡住整条队列。

## 后台接口口径

采集接口：

```text
/api/cellPhone/26/apply/examinePage
```

详情接口：

```text
/api/cellPhone/26/apply/detail
```

回显接口：

```text
/api/cellPhone/26/apply/machineApproval
```

回显通过：

```json
{"status": 1}
```

回显不通过：

```json
{"status": 2, "refuseMessage": "UTF-8中文原因"}
```

上线第一轮必须确认 `examinePage` 只返回真正待机审订单；当前主线请求发送 `status=0` 和 `machineExamineStatus=0`，返回订单侧只接受 `machineExamineStatus` 为空、`null` 或空字符串的订单继续审核。

## 回滚

紧急回滚优先停调度：

```bash
sudo systemctl stop guobu-auto-audit
```

或在 XXL-JOB 中停用任务。

若只关闭图片真实性：

```bash
PHOTO_AUTHENTICITY_MODE=off
```

若只关闭条码救回：

```bash
SN_BARCODE_MODE=off
```

生产入口脚本默认会固定生产口径；如果需要回滚开关，必须由负责人确认后修改启动脚本或使用受控发布包，不建议部署人员临时改业务代码。

## 验收标准

- 包内没有 `.env`、密钥、token、SQLite 状态库、日志、缓存、样本库、旧压缩包、`.git`、`.worktrees`。
- `bash deploy/linux/validate_deployment.sh` 通过。
- 连续 3 轮空转不报错。
- 放入 1-3 单待机审订单后，能抓取、审核、回显。
- 不通过订单的 `refuseMessage` 是 UTF-8 中文，不乱码。
- SN 不一致订单能触发条码阶段。
- 图片真实性本地树关闭。
- 家电 SN 冲突救回默认开启。

# 国补审核主线 Linux 生产部署说明

先读 `deploy/linux/00_README_FIRST.md`。本文件用于解释细节和验收口径。

## 交付内容

本包是国补审核主线生产包。部署人员配置 `.env` 后运行脚本，不需要修改业务代码。

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

## 傻瓜式命令顺序

```bash
cd /opt/audit_robot
bash deploy/linux/install.sh
bash deploy/linux/configure_env.sh
bash deploy/linux/preflight.sh
bash deploy/linux/run_once.sh
```

安装脚本会创建默认运行用户 `auditrobot`，并创建 `/var/lib/audit_robot/state` 和 `/tmp/audit_robot_guobu`。

run_once.sh 只用于部署验收，执行一轮后退出。tools/start_guobu_linux_auto_audit.sh 默认是循环模式，只有传入 `--once` 才执行一轮后退出。

如果使用 systemd 常驻：

```bash
bash deploy/linux/install_systemd.sh
bash deploy/linux/start.sh
```

如果使用 XXL-JOB，把 `deploy/linux/xxl_job_command.txt` 内容配置到调度器。

XXL-JOB 和 systemd 只能二选一，不能同时开启。

## 配置文件

交互配置：

```bash
bash deploy/linux/configure_env.sh
```

手工配置：

```bash
cp deploy/linux/.env.example .env
chmod 600 .env
vi .env
```

必须填写：

- `VISION_API_BASE_URL`：模型接口地址
- `VISION_API_KEY`：模型密钥
- `GUOBU_AUTH_TOKEN`：后台 Authorization
- `MACHINE_APPROVAL_AUTH_TOKEN`：后台 Authorization，可与 `GUOBU_AUTH_TOKEN` 相同

如果 Authorization 带空格，例如 `Bearer xxxxxx`，手工编辑时必须加单引号：

```bash
GUOBU_AUTH_TOKEN='Bearer xxxxxx'
```

真实 `.env` 不得进入 Git、日志、报告或工单。

## 启动前验证

```bash
bash deploy/linux/preflight.sh
```

预检只检查环境，不抓单、不审核、不回显。输出里密钥和 token 只能显示 `set`，不得出现真实值。

预检还会检查：

- 包目录没有 `.env`、SQLite、日志、缓存、Git 元数据等禁入项
- `.env` 权限是 `600` 或 `400`
- Node.js 版本不低于 18
- 状态目录和临时目录可写

## 单轮验证

```bash
bash deploy/linux/run_once.sh
```

空转验收：后台没有待审核订单时，允许 `fetched_count=0`、`processed_count=0`，进程必须成功结束。

run_once.sh 只用于部署验收，执行一轮后退出；不要把它当成生产常驻进程。

故障验收：接口失败或回显最终失败不能伪装成空转，必须进入输出 `errors` 或失败计数。

## XXL-JOB 对接

命令文件：`deploy/linux/xxl_job_command.txt`

XXL-JOB 命令也是单轮执行，由调度器每 10 分钟拉起一次。调度器负责循环，脚本本身跑完一轮就退出。

调度建议：

- 周期：10 分钟一次
- 阻塞策略：单机串行，禁止并发重叠
- 调度重试：0 或 1 次
- 超时：大于单轮真实审核最大耗时

## systemd 常驻

systemd/start.sh 是常驻循环模式，适合不使用 XXL-JOB 的服务器。

安装：

```bash
bash deploy/linux/install_systemd.sh
```

启动：

```bash
bash deploy/linux/start.sh
```

状态：

```bash
bash deploy/linux/status.sh
```

日志：

```bash
bash deploy/linux/logs.sh
```

停止：

```bash
bash deploy/linux/stop.sh
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

## 紧急停止与回滚

紧急停止：

```bash
bash deploy/linux/emergency_stop.sh
```

如果使用 XXL-JOB，还必须在 XXL-JOB 控制台停用任务。

本包不提供“修改 `.env` 自动切 off”的业务回滚，因为生产启动脚本会固定 enforce 口径，这是为了防止部署环境误改导致主线漂移。业务规则回滚必须使用负责人确认后的回滚包或专门脚本。

## 验收标准

- 包内没有 `.env`、密钥、token、SQLite 状态库、日志、缓存、样本库、旧压缩包、`.git`、`.worktrees`。
- `.env` 权限是 `600`。
- `bash deploy/linux/preflight.sh` 通过。
- systemd 使用非 root 用户运行。
- `bash deploy/linux/run_once.sh` 空转不报错。
- 连续 3 轮空转不报错。
- 放入 1-3 单待机审订单后，能抓取、审核、回显。
- 不通过订单的 `refuseMessage` 是 UTF-8 中文，不乱码。
- SN 不一致订单能触发条码阶段。
- 图片真实性本地树关闭。
- 家电 SN 冲突救回默认开启。

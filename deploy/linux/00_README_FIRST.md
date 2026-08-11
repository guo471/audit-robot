# 先读我：国补审核傻瓜式部署步骤

## 一句话说明

部署人员只需要解压本包、填写 `.env`、按下面命令逐条执行。不要修改业务代码。

## 第 1 步：放到服务器

推荐目录：

```bash
/opt/audit_robot
```

如果放到其他目录，systemd 安装脚本会自动使用当前目录。

## 第 2 步：安装依赖

```bash
cd /opt/audit_robot
bash deploy/linux/install.sh
```

成功后会看到：

```text
Install complete
```

安装脚本会创建运行用户 `auditrobot`，并创建状态库和临时目录。

## 第 3 步：填写配置

推荐使用交互脚本，密钥输入不会回显：

```bash
bash deploy/linux/configure_env.sh
```

也可以手工复制模板：

```bash
cp deploy/linux/.env.example .env
chmod 600 .env
vi .env
```

必须填写模型地址、模型密钥、后台 Authorization。

## 第 4 步：启动前检查

```bash
bash deploy/linux/preflight.sh
```

通过标准：

- 环境变量只显示 `set`，不显示真实值
- Python 依赖可导入
- Node.js 可用
- 不抓订单
- 不审核
- 不回显

## 第 5 步：先跑一轮

```bash
bash deploy/linux/run_once.sh
```

run_once.sh 只用于部署验收，执行一轮后退出。它不是生产常驻进程。

空转也算成功：没有待审核订单时，`fetched_count=0`、`processed_count=0`，不能报错。

## 第 6A 步：接入 XXL-JOB

把 `deploy/linux/xxl_job_command.txt` 里的命令配置到 XXL-JOB。

要求：

- 10 分钟一次
- 单机串行
- 禁止并发重叠
- 调度重试 0 或 1 次

XXL-JOB 命令也是单轮执行，由调度器每 10 分钟拉起一次。

## 第 6B 步：不用 XXL-JOB 时安装 systemd

```bash
bash deploy/linux/install_systemd.sh
bash deploy/linux/start.sh
```

systemd/start.sh 是常驻循环模式。tools/start_guobu_linux_auto_audit.sh 默认是循环模式，只有传入 `--once` 才执行一轮后退出。

XXL-JOB 和 systemd 只能二选一，不能同时开启。

查看状态：

```bash
bash deploy/linux/status.sh
```

查看日志：

```bash
bash deploy/linux/logs.sh
```

停止：

```bash
bash deploy/linux/stop.sh
```

## 紧急停止

```bash
bash deploy/linux/emergency_stop.sh
```

如果使用 XXL-JOB，还必须在 XXL-JOB 控制台停用任务。

## 生产口径

本包默认开启：

```text
SN_POLICY_VERSION=v2
SN_BARCODE_MODE=enforce
SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE=true
DIGITAL_ACTIVATION_EVIDENCE_MODE=on
PHOTO_AUTHENTICITY_MODE=enforce
PHOTO_AUTHENTICITY_NEW_RULE_ENABLED=true
PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED=false
PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED=false
```

## 部署验收标准

- ZIP 校验值正确
- 包内没有 `.env`、密钥、token、SQLite、日志、缓存、样本库、Git 元数据
- `.env` 权限是 `600`
- `bash deploy/linux/preflight.sh` 通过
- systemd 使用非 root 用户运行
- `bash deploy/linux/run_once.sh` 空转不报错
- 灰度订单能完成采集、审核、回显
- 不通过订单 `refuseMessage` 中文不乱码
- SN 不一致订单能触发条码阶段
- 图片真实性本地树关闭
- 家电 SN 冲突救回默认开启

# Linux 生产部署验收清单

本清单用于生产服务器上线前逐项验收。所有项目通过后，才允许开启正式自动审核。

## 包完整性

- [ ] 记录生产 ZIP 文件名：________________
- [ ] 记录 SHA256：________________
- [ ] 校验同目录 `.SHA256.txt` 或负责人提供的 SHA256 与实际 ZIP SHA256 一致。
- [ ] 解压后存在 `audit_robot/deploy/linux/00_README_FIRST.md`。
- [ ] 解压后存在 `audit_robot/deploy/linux/README_DEPLOY.md`。
- [ ] 解压后存在 `audit_robot/deploy/linux/PRODUCTION_ACCEPTANCE_CHECKLIST.md`。
- [ ] 包内没有 `.env`、SQLite、日志、缓存、旧 ZIP、`.git`、`.worktrees`。
- [ ] 包内没有训练、评估、研究、样本库文件。

## 环境与权限

- [ ] 项目目录放在 `/opt/audit_robot` 或已确认实际部署目录。
- [ ] 已执行 `bash deploy/linux/install.sh`。
- [ ] `.env 权限` 为 `600`。
- [ ] `.env` 不出现在 Git、日志、报告和工单中。
- [ ] 运行用户为 `auditrobot`，不是 root。
- [ ] `auditrobot` 可读取 `.env`。
- [ ] `auditrobot` 可写 `/var/lib/audit_robot/state`。
- [ ] `auditrobot` 可写 `/tmp/audit_robot_guobu`。
- [ ] 已执行 `bash deploy/linux/preflight.sh`，结果通过。

## 启动模式

- [ ] 已确认 `run_once.sh` 只用于部署验收，执行一轮后退出。
- [ ] 已确认 `XXL-JOB` 命令也是单轮执行，由调度器每 10 分钟拉起一次。
- [ ] 已确认 `systemd/start.sh` 是常驻循环模式。
- [ ] 已确认 `XXL-JOB` 和 `systemd` 只能二选一，不能同时开启。
- [ ] 如果使用 XXL-JOB，已配置单机串行、禁止并发重叠。
- [ ] 如果使用 systemd，已执行 `bash deploy/linux/install_systemd.sh` 和 `bash deploy/linux/start.sh`。

## 空转与灰度

- [ ] 已执行 `bash deploy/linux/run_once.sh`，空转不报错。
- [ ] 已连续 3 轮空转，不报错、不退出异常。
- [ ] 已放入 1-3 单灰度订单。
- [ ] 灰度订单可完成采集、审核、回显。
- [ ] 通过订单回显 `status=1`。
- [ ] 不通过订单回显 `status=2`。
- [ ] 不通过订单 `refuseMessage` 是 UTF-8 中文，不乱码。

## 业务开关确认

- [ ] `SN_POLICY_VERSION=v2`。
- [ ] `SN_BARCODE_MODE=enforce`。
- [ ] SN 不一致订单能触发条码阶段。
- [ ] `SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE=true`。
- [ ] `DIGITAL_ACTIVATION_EVIDENCE_MODE=on`。
- [ ] `PHOTO_AUTHENTICITY_MODE=enforce`。
- [ ] `PHOTO_AUTHENTICITY_NEW_RULE_ENABLED=true`。
- [ ] `PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED=false`。
- [ ] `PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED=false`。

## 应急与签字

- [ ] 已验证 `bash deploy/linux/emergency_stop.sh` 可停止服务。
- [ ] 如果使用 XXL-JOB，已确认控制台可停用任务。
- [ ] 已确认日志不输出 Authorization、模型密钥、真实 token。
- [ ] 验收人：________________
- [ ] 日期：________________

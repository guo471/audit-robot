# 图片真实性审核默认开启设计

## 目标

正式使用的 `hybrid` 主线审核在调用方没有提供真实性模式时，默认启用图片真实性审核并执行证据转人工规则，避免静默回落到 `off`。`fast`、`v2`、`sn_only` 旧模式保持原样。

## 行为

- `PhotoAuthenticityConfig.from_env()` 在 `PHOTO_AUTHENTICITY_MODE` 缺失时使用 `enforce`。
- 命令行参数 `--photo-authenticity-mode` 在参数和环境变量均缺失时使用 `enforce`。
- 显式设置 `PHOTO_AUTHENTICITY_MODE=off` 或传入 `--photo-authenticity-mode off` 时仍关闭真实性审核，作为回退机制。
- `shadow` 行为保持不变。
- FFT默认保持关闭；只有显式设置 `PHOTO_AUTHENTICITY_FFT_ENABLED=true` 才启用。
- 商品自身屏幕的局部摩尔纹严格豁免保持不变，不单独转人工。
- 合规模型返回的真实性结构缺失或不合法时直接转人工，不再发起局部大模型补问。
- 不开启深度思考，不修改后台订单状态。

## 实现范围

- 修改 `tools/photo_authenticity_mainline.py` 的配置默认值。
- 修改 `tools/run_guobu_model_audit_v2.py` 的命令行默认值和帮助文字。
- 修改 `tools/run_guobu_model_audit_v2.py` 的 `hybrid` 调用，禁止真实性局部补问。
- 更新相关项目记忆说明。
- 不修改真实性证据定义、提示词内容或转人工判定逻辑。
- 不把真实性审核扩展到 `fast`、`v2` 或 `sn_only`。

## 验证

- 无环境变量时，配置对象的模式为 `enforce`。
- 环境变量为 `off` 或 `shadow` 时，显式值优先。
- 命令行不传模式时默认 `enforce`；显式传 `off` 时仍可回退。
- FFT无显式开关时仍为关闭。
- 商品自身屏幕局部摩尔纹继续豁免。
- 真实性结构缺失时转人工且模型调用次数不增加。
- 运行相关单元测试及完整测试集，确认没有回归。

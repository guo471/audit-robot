# 非实拍单图检测专项完整业务交接

交接日期：2026-07-15

## 1. 业务目标

识别审核图片是否为直接场景实拍，或对屏幕、照片、打印物、图片窗口等载体的二次拍摄。业务输出只有三类：

- `high_risk_non_real`：具有明确外部载体或跨区域屏摄强证据。
- `manual_review`：存在风险证据或由FFT补救，需要人工确认。
- `no_evidence`：当前没有足够可见证据。

验收线：非实拍进入 high/manual 至少90%，实拍进入 high/manual 不超过6%。实拍误杀约6张/100张可接受；非实拍优先转人工，不追求全部自动判high。

## 2. 用户约束与工作纪律

- 394张非实拍全部是用户确认非实拍，不能只统计某个59张子目录。
- 385张实拍全部是用户清洗确认实拍。
- 开发时每轮100张非实拍+100张实拍，方法达标后才使用剩余样本。
- 不去重；指标按物理图片统计，但必须使用SHA和订单组避免跨批泄漏。
- 禁止使用文件名、目录、尺寸、格式、订单、商品型号、水印文字或记住图片作为分类特征。
- 千问必须关闭深度思考：`enable_thinking=false`。
- 方法无效要主动淘汰，不沿错误路线继续消耗API成本。
- API凭据只从环境变量读取，文档不保存任何密钥。

## 3. 前期做了什么

### 3.1 数据冻结

- 冻结清单：`reports/non_real_photo_agent_v4/freeze_20260714_v1/manifest.csv`
- 总量：394非实拍、385实拍。
- 非实拍包含340个唯一SHA；存在重复内容，但不从业务指标中去重。
- 使用 `group_key/group_id + SHA-256` 并查集构造传递组件，避免相同订单或重复内容跨开发/验证批。
- 固定开发批：`reports/non_real_photo_agent_v4/dev100-v1/`，100非实拍+100实拍。

### 3.2 提示词路线

最终保留的V4提示词结构：

1. 分别检查上、右、下、左四边：场景连续、载体边界、异常截断、不可见或不确定。
2. 先判断界面归属：商品屏、外部屏幕、无或不确定。
3. 强证据：外部屏幕/相片载体、外部照片查看器UI、打印照片边界、嵌套图像边界、跨两个以上非商品屏区域的同一摩尔纹。
4. 弱证据：边缘截断、外层光学平面、整体平面感、局部摩尔纹、UI候选。
5. 商品自身屏幕中的界面和局部摩尔纹豁免；普通模糊、反射、摆拍、印刷纹理不能单独升级。
6. 最终结果由程序机械规则派生，模型只报告可见观察，减少自由发挥。

提示词文件：`photo_authenticity/prompts/non_real_photo_auditor_v4.txt`。

### 3.3 被淘汰的路线

- 宽边缘/四角二次复核：非实拍提升到86%，但实拍干预升到17%，成本约翻倍；淘汰。
- 单请求九视图：注意力稀释，检查点非实拍仅12.5%；淘汰。
- V4.1纯提示词强化：困难样本无可靠grounded recovery，旋转/裁切反事实不稳定；停止prompt-only扩张。
- EfficientNet embedding：只补救少量非实拍，明显弱于FFT；淘汰。
- Fusion：与FFT召回相同，没有达到预注册的召回提升门槛，却增加复杂度；淘汰。

## 4. 最终方案如何形成

V4固定开发批最初结果：

- 非实拍76/100。
- 实拍干预4/100。

因此增加独立FFT补救通道：

- 输入仅为EXIF转正后解码的RGB像素。
- 对Y/Cb/Cr三通道、全图和2×2区域计算频域统计，共795维。
- LogisticRegression仅可把V4 `no_evidence`转为`manual_review`。
- 评估使用grouped nested OOF，PCA/标准化只能在训练折拟合；最终FFT路线本身不使用embedding/PCA。
- 修正one-standard-error选择、PCA规则、OOF fold字段和真实延迟后，严格开发结果为非实拍97/100、实拍5/100。

第一隔离验证批使用阈值0.821501：

- 非实拍生产路由99/100。
- 实拍干预9/100，正式不通过。
- 原因：V4已误伤6张，FFT又增加3张。

只做一个调整：把FFT阈值提高到圆整的`0.995`，不改变模型、特征或提示词。该调整在第一批只作为诊断，不能冒充通过。

第二个与开发批、第一验证批均零组件/SHA重叠的100+100验证：

- 非实拍95/100。
- 实拍干预6/100。
- 执行失败0。
- 两名独立专家均复算并批准。

## 5. 最终冻结链路

```text
单张审核图片
  -> V4单图证据审查（qwen3.7-plus，thinking=false）
  -> 程序机械派生 high/manual/no_evidence
  -> high/manual：保持原结果
  -> no_evidence：提取795维FFT像素频域特征
  -> score >= 0.995：manual_review
  -> score < 0.995：no_evidence
  -> FFT本地失败两次：fail-closed manual_review
```

关键不变量：

- FFT绝不能新增`high_risk_non_real`。
- V4已有high/manual绝不能被FFT修改。
- 只有`no_evidence -> manual_review`合法。
- 运行失败与模型语义识别分别统计；故障转人工不能冒充模型命中。

## 6. 最终业务成绩

统一阈值0.995，开发批采用严格OOF而非训练拟合结果：

| 分区 | 非实拍 | 实拍干预 |
|---|---:|---:|
| 固定开发批 | 92/100 | 5/100 |
| 第一验证批按0.995反事实复算 | 96/100 | 6/100 |
| 第二独立验证批实际运行 | 95/100 | 6/100 |
| 全部最后剩余样本 | 81/94 | 3/85 |
| 全库语义口径 | **364/394，92.39%** | **20/385，5.19%** |

第一验证批另有2张非实拍因执行失败安全转人工；计入生产实际路由后为366/394，92.89%。

必须披露：最后剩余批是排除前三批后的困难尾部，只有22个组件、47个唯一SHA，非实拍召回86.17%。整体达标不代表每个子分布都稳定超过90%。

## 7. 性能和资源

- V4通常约5.3秒/张。
- FFT P50约0.45至0.97秒/张，P95约1.21至1.44秒/张。
- 全阶段记录约2,376,675 tokens。
- 未冻结API价格表，无法严谨换算人民币成本。
- 深度思考始终关闭。

## 8. 文件和代码交接

隔离工作树：

`C:\Users\HUAWEI\Desktop\audit_robot\.worktrees\non-real-photo-agent-v1`

分支：

`codex/non-real-photo-agent-v1`

关键代码：

- `tools/fft_embedding_rescue_classifier.py`
- `tools/run_full_rescue_classifier_experiment.py`
- `tools/freeze_fft_rescue_classifier.py`
- `tools/derive_fft_threshold_artifact.py`
- `tools/run_frozen_fft_validation.py`
- `tools/select_validation100_v1.py`
- `tools/select_final_remainder_v1.py`
- `tools/merge_validation_v4_results.py`

冻结模型：

`reports/non_real_photo_classifier/dev100-v1/frozen_fft_v2_threshold995`

验证证据：

- `reports/non_real_photo_classifier/validation100-v1/`
- `reports/non_real_photo_classifier/validation100-v2/`
- `reports/non_real_photo_classifier/final-remainder-v1/`
- `.superpowers/sdd/task-10-final-validation-report.md`

关键提交：

- `400382f`：冻结和验证FFT救援分类器。
- `c22533c`：隔离v2验证与阈值派生。
- `adcdd49`：打包全部最后剩余样本。
- `d3491b3`：允许非200张冻结批安全合并。
- `2d29ec6`：最终验证报告。

专项验证命令：

```powershell
& 'C:\Users\HUAWEI\Desktop\audit_robot\.venv-photo-auth\Scripts\python.exe' `
  -m pytest `
  tests/test_fft_embedding_rescue_classifier.py `
  tests/test_freeze_fft_rescue_classifier.py `
  tests/test_validation100_pipeline.py -q
```

最后一次运行结果：46项专项测试通过。

## 9. 如何接入主线

当前专项尚未自动接入`tools/run_guobu_model_audit_v2.py`。建议接入顺序：

1. 先在主线配置中增加可关闭的`photo_authenticity_v4_fft`开关，默认关闭。
2. 商品照片、拆封照片、激活照片逐张运行V4；任何一张high/manual则订单转人工并保留图片级证据。
3. 只有V4 N0图片才运行本地FFT，避免增加视觉API调用。
4. Excel新增图片真实性结果、V4规则码、FFT分数、阈值、证据摘要、执行状态。
5. 网络/API失败与本地FFT失败分别统计；按业务授权决定订单级fail-closed策略。
6. 用一批真实订单做影子审核，确认订单级人工率后再考虑正式启用。

未经明确授权，不要直接把专项逻辑并入生产审核或修改后台订单。

## 10. 后续优化方向

当前13张困难尾部漏放实际只有7个唯一内容：

- 9张的FFT分数在0.914至0.988，具有细微全局点阵/彩色摩尔纹，但被0.995阈值截断。
- 2张是大面积低纹理家电场景，周期峰强但语义证据弱。
- 2张是手机/包装近拍，外部载体边框不可见，证据不足。

下一候选路线：仅对V4 N0且FFT分数位于`[0.95, 0.995)`的图片做局部语义复核，必须明确指出同方向、同间距的纹理跨越两个不同物理区域才转人工。当前全部779张均已曝光，该路线只能在全新外部实拍/非实拍样本上验收。

禁止事项：

- 不得直接把阈值降至0.90或0.95；全库实拍干预会超过6%。
- 不得用商品屏全豁免；开发非实拍召回会明显下降。
- 不得继续在现有779张上调参后宣称盲测成功。
- 不得把定位水印文字、文件名、目录、尺寸、商品型号作为非实拍特征。

# 主线图片真实性合并审核设计

日期：2026-07-15

## 1. 目标

将已验证的单图V4真实性观察规则和FFT 0.995补救机制并入国补审核主线，优先复用第二次“图片合规检查”模型调用，避免为每张图片新增一次独立视觉模型请求。

目标约束：

- 只对现有主线准备自动放行的订单执行真实性阶段。
- 已因地址、SN、缺图、品类或合规问题转人工的订单跳过真实性增量处理。
- 不修改后台订单，只影响影子审核结果和输出报表。
- 千问固定`enable_thinking=false`。
- 模型只输出结构化可见观察，最终真实性判定由程序机械完成。
- 本地FFT只能执行`no_evidence -> manual_review`，阈值固定`0.995`。
- 所有新增能力必须可按配置独立关闭，并能回退到当前主线行为。

## 2. 接入位置

现行主线：

```text
本地预检
→ SN专项模型调用
→ 本地SN严格比较
→ 图片合规模型调用
→ 程序规则归一化
→ 最终订单结果
```

合并后：

```text
本地预检
→ SN专项模型调用
→ 本地SN严格比较
→ 图片合规模型调用（新增逐图真实性观察）
→ 程序校验原合规字段
→ 程序逐图机械派生V4真实性结果
→ 仅对V4 no_evidence图片运行本地FFT
→ 汇总订单真实性结果
→ 合并原有原因码
→ 最终订单结果和Excel
```

真实性逻辑插在第二次模型调用完成之后、订单最终自动放行之前。

## 3. 运行模式和回退

新增配置`PHOTO_AUTHENTICITY_MODE`：

- `off`：完全保持当前主线，不要求新字段，不运行FFT。
- `shadow`：运行真实性链路并记录结果，但不改变订单放行/转人工结果。
- `enforce`：真实性命中或最终失败时可将准备放行订单转人工。

默认值必须为`off`。首次部署只允许使用`shadow`。只有影子验收通过后才可改为`enforce`。

新增配置：

- `PHOTO_AUTHENTICITY_FALLBACK=independent_v4|manual|skip`
- `PHOTO_AUTHENTICITY_FFT_THRESHOLD=0.995`
- `PHOTO_AUTHENTICITY_ARTIFACT_DIR=<冻结artifact目录>`
- `PHOTO_AUTHENTICITY_MAX_FALLBACK_CALLS_PER_ORDER=1`

回退操作：把`PHOTO_AUTHENTICITY_MODE`改为`off`即可恢复当前主线结果。回退不删除真实性缓存和影子证据，便于问题分析。

## 4. 合规提示词新增内容

现有品类规则、SN边界和原因码规则保持不变。第二次模型调用新增逐图真实性任务：

1. 每张图片分别检查上、右、下、左四边。
2. 每张图片分别判断`screen_owner`。
3. 强证据只允许：
   - `EXTERNAL_PHOTO_CARRIER`
   - `PHOTO_VIEWER_UI`
   - `PRINTED_PHOTO_CARRIER`
   - `NESTED_IMAGE_BOUNDARY`
   - `CROSS_OBJECT_MOIRE`
4. 弱证据只允许：
   - `EDGE_CUTOFF`
   - `OUTER_PLANE_OPTICS`
   - `PLANAR_APPEARANCE`
   - `LOCAL_MOIRE`
   - `UI_CANDIDATE`
5. 商品自身屏幕内的界面和局部摩尔纹不构成外部屏摄强证据。
6. 跨对象摩尔纹必须在同一张图片中跨越两个不同的非商品屏物理区域。
7. 不允许把不同图片的证据合并。

新增JSON字段：

```json
{
  "photo_authenticity_by_image": [
    {
      "image_id": "",
      "edges": {
        "top": "scene_continues | carrier_boundary | abrupt_cutoff | not_visible | uncertain",
        "right": "",
        "bottom": "",
        "left": ""
      },
      "screen_owner": "product_screen | external_screen | none | uncertain",
      "strong_evidence": [
        {"code": "", "regions": []}
      ],
      "weak_evidence": [
        {"code": "", "regions": []}
      ],
      "reason": ""
    }
  ]
}
```

模型不得输出最终真实性判定作为裁决依据；即使输出，也必须忽略。

## 5. 程序机械判定

逐图规则保持冻结V4语义：

- 明确外部照片载体：high。
- 外部屏幕照片查看器UI：high。
- 打印照片载体或嵌套图片边界：high。
- 同一摩尔纹跨至少两个非商品屏区域：high。
- 至少两条`carrier_boundary`：high。
- 一条`carrier_boundary`：manual。
- `abrupt_cutoff + OUTER_PLANE_OPTICS`：high。
- 其他有效强/弱证据或异常截断：manual。
- 无证据：no_evidence。

商品屏内`PHOTO_VIEWER_UI`在且仅在全部区域均为`product_screen`时豁免。

## 6. FFT补救

- 只处理V4为`no_evidence`的图片。
- 输入只允许EXIF转正后的解码RGB像素。
- extractor固定为`fft-v1-512-ycbcr-5x53`，795维。
- 冻结模型SHA-256：`49352975e2ef36d3723cbe6fe028687a56101920fef50becc744c65b96aa512b`。
- 阈值固定`0.995`。
- 分数达到阈值只能转`manual_review`，不得新增high。
- 本地执行最多尝试两次；仍失败按fallback策略处理。

## 7. 字段缺失和独立V4兜底

第二次模型调用必须覆盖本订单传入的全部`image_id`。以下情况视为真实性结构异常：

- 缺少`photo_authenticity_by_image`。
- 数量或`image_id`集合与输入不一致。
- 字段类型、枚举值或证据区域非法。
- 同一`image_id`重复。

默认兜底`independent_v4`：

- 每单最多对一张结构异常图片追加独立V4调用。
- 若多张缺失，优先第一张缺失图片；订单记录结构异常，剩余缺失图片按`manual`处理，避免调用失控。
- 独立调用仍失败时，`shadow`只记录失败；`enforce`转人工。

兜底调用必须有独立缓存，缓存键包含图片SHA、提示词SHA、模型和schema版本。

## 8. 订单级汇总

只在订单原结果准备自动放行时应用：

- 任一图片high：订单真实性命中。
- 任一图片manual：订单真实性命中。
- 任一图片FFT补救：订单真实性命中。
- `shadow`模式只记录`would_manual=true`，不改原结果。
- `enforce`模式将订单转人工。

新增原因码：

- `NON_REAL_PHOTO_STRONG_RISK`
- `NON_REAL_PHOTO_REVIEW`
- `NON_REAL_PHOTO_FFT_RESCUE`
- `PHOTO_AUTHENTICITY_SERVICE_FAILURE`

不得把弱证据统一伪装成现有`IMAGE_STRONG_RISK`。

## 9. 输出和审计证据

订单明细新增：

- `photo_authenticity_mode`
- `photo_authenticity_would_manual`
- `photo_authenticity_final_result`
- `photo_authenticity_reason_code`
- `photo_authenticity_image_results_json`
- `photo_authenticity_v4_elapsed_sec`
- `photo_authenticity_fft_elapsed_sec`
- `photo_authenticity_total_tokens`
- `photo_authenticity_fallback_calls`
- `photo_authenticity_execution_status`

每张图片保留：

- image_id和图片角色。
- V4机械结果、规则码、边缘、screen_owner、强弱证据和原因。
- FFT分数、阈值、频域证据摘要。
- 是否缓存、耗时、token和执行状态。

## 10. 时间和成本预期

现有200单中169单准备放行，共527张图片。

合并模式预计：

- 不增加常规独立V4调用。
- 第二次模型调用因提示词和输出增长，预计新增约15万至30万tokens。
- 本地FFT预计增加约4分钟。
- 第二次模型输出增长预计增加约2至8分钟。
- 整批总增加预计6至12分钟；现有约48分钟，合并后预计54至60分钟。

结构异常时的独立V4兜底必须限制为每单最多一次，不计入常规预估。

## 11. 验证和上线门槛

### 离线结构与准确率验证

- JSON schema成功率>=99%。
- 每张输入图片均有唯一结果。
- 非实拍拦截>=90%。
- 实拍图片干预<=6%。
- 不允许跨图片组合证据。
- FFT转换不变量违规数为0。

### 当前200单影子回放

- 不改变原169放行/31人工结果。
- 新增转人工候选不超过20/169。
- 总新增耗时不超过12分钟。
- 新增tokens不超过30万。
- 真实性结构异常率<=1%。
- 最终服务失败率<=1%。

### 渐进启用

1. 全量shadow。
2. 10% enforce。
3. 50% enforce。
4. 100% enforce。

任一阶段超出订单人工率、失败率或耗时门槛，立即切回`off`或`shadow`，保留证据后重新分析。

## 12. 稳定性边界

- 已验证的单图独立方案总体非实拍语义拦截92.39%、实拍干预5.19%。
- 合并提示词属于新调用形态，不能直接继承该指标，必须重新验证。
- 当前779张均已用于开发或验证，只能用于回归比较；后续泛化结论需要新样本。
- 最后困难尾部批非实拍召回86.17%，不能宣称所有子分布稳定>=90%。
- 合并方案若低于验收线，回退到`off`，备选为独立V4四路并发或重新设计局部真实性复核。

## 13. 非目标

- 本阶段不修改后台订单状态。
- 不修改SN识别和比较逻辑。
- 不改变现有商品、拆封、激活合规规则。
- 不降低FFT阈值。
- 不新增文件名、目录、尺寸、商品型号或水印文字特征。

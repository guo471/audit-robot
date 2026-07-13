# 单张图片非实拍审核 Agent 设计

日期：2026-07-13

## 1. 目标

创建一个独立的单张图片非实拍审核 Agent，用于在不接入现有国补主审核链路的前提下，单独测试静态图片中的非现场实拍风险。

第一阶段使用项目当前完整非实拍样本集测试拦截能力，后续使用人工确认的实拍图片测试高风险误杀率和人工复核率。样本数量和已知标签只用于离线评估，不写入 Agent 提示词。

## 2. 范围

Agent 每次只接收一张图片，不读取订单信息、SN、商品类型、其他图片或历史判断。

检测范围：

- 对电脑、手机、平板、电视等电子屏中旧照片的再次拍摄；
- 截图、相册图、浏览器图或图片查看器中的证据照片；
- 一张照片嵌套在另一张照片中；
- 对打印照片的再次拍摄。

本版不负责：

- 商品合规、拆封、安装或激活审核；
- SN、IMEI、序列号识别或一致性比较；
- 地址、时间、水印内容合理性判断；
- 根据其他图片进行跨图判断；
- 声称能够可靠识别所有 AI 生成图或精细图像篡改；
- 自动通过、自动驳回或修改生产订单状态。

## 3. 三态结果

- `high_risk_non_real`：存在直接证据，或至少两个不同证据族的支持证据。
- `manual_review`：不存在直接证据，但存在恰好一个合格支持证据。
- `no_evidence`：只有弱证据，或没有发现任何非实拍证据。

`no_evidence` 只表示当前图片中未发现规定范围内的非实拍证据，不表示已经证明真实现场拍摄。

在非实拍测试集中，前两类均计为拦截，但必须分别统计；在实拍测试集中，`high_risk_non_real` 计为高风险误杀，`manual_review` 计为人工复核率，不得只报告合并结果。

## 4. 证据模型

### 4.1 直接证据

任一直接证据成立即输出 `high_risk_non_real`：

- `EXTERNAL_PHOTO_CARRIER`：明确看到外部电子屏正在承载完整商品或现场照片。
- `PHOTO_VIEWER_CONTAINER`：明确看到相册、浏览器、图片查看器、窗口、画布、滚动条、鼠标指针或系统栏正在包围或承载完整证据照片。
- `NESTED_PHOTO_BOUNDARY`：明确看到内部照片的独立边界，边界内包含商品、包装、背景或原有水印。
- `PRINTED_PHOTO_CARRIER`：明确看到照片纸边缘、翘曲或折痕，且完整现场照片位于纸面内。

界面严格位于被审核商品自身屏幕内部时，不得仅因出现任务栏、浏览器、设置页或相册而认定直接证据。

### 4.2 支持证据

同一证据族最多计一次：

- `CROSS_REGION_SAMPLING`：规则点阵、扫描线、RGB 子像素、周期性色带或摩尔纹连续覆盖至少两个材质或深度不同的非屏幕区域；纹理跨越物体边界后方向和空间周期基本不变；且不能由包装印刷、条码、织物、地砖或局部反光合理解释。
- `OUTER_PLANE_OPTICS`：同一层玻璃反光、眩光或亮度渐变同时覆盖照片中原本处于不同深度的多个区域，表现为附着在整张图像上方的统一平面。
- `DISPLAY_EDGE_OR_CROP`：出现可疑显示区域边缘、连续屏幕黑边、画布边缘或显示裁切痕迹，但外部载体未完整露出。

高风险所需的两项支持证据必须来自不同证据族。同一摩尔纹现象的不同描述不得重复计数。

### 4.3 弱证据

以下现象只能记录，单独出现或同时出现均不得升级结论：

- 普通模糊、低清晰度、过曝、欠曝或倾斜；
- GPS、日期、地址或手机号水印；
- 单一区域轻微摩尔纹；
- 商品自身屏幕内部的像素纹或扫描纹；
- 包装印刷网点、条码锯齿、织物纹理；
- 塑料膜、玻璃、金属或柜台的局部反光；
- 单独的黑边、白边或主体占比过高；
- 内容像宣传图、背景整洁或构图规整。

## 5. 商品自身屏幕豁免

只有同时满足以下条件，才能应用商品自身屏幕豁免：

- 能定位商品屏幕边界；
- 异常纹理严格限制在边界内部；
- 屏幕与商品实体的结构和透视连续；
- 没有图片查看器画布、嵌套照片边界或第二层载体迹象。

SN、IMEI、激活页、设置页或关于本机页本身既不是非实拍证据，也不能单独证明屏幕属于当前现场商品。

## 6. 决策顺序

1. 存在任一直接证据：`high_risk_non_real`。
2. 不存在直接证据，但存在至少两个不同证据族的支持证据：`high_risk_non_real`。
3. 不存在直接证据，且恰好存在一个支持证据：`manual_review`。
4. 只有弱证据或没有证据：`no_evidence`。

不得为了谨慎而将只有弱证据的图片输出为 `manual_review`。

## 7. 输出接口

Agent 只输出一个可解析 JSON 对象，不输出 Markdown、代码围栏、前言、推理过程或额外字段。

```json
{
  "result": "high_risk_non_real | manual_review | no_evidence",
  "carrier_observation": "electronic_screen | printed_photo | nested_image | none_observed | uncertain",
  "direct_evidence": [],
  "supporting_evidence": [],
  "weak_evidence": [],
  "affected_non_screen_regions": [],
  "product_screen_exemption_applied": false,
  "reason": "不超过80个中文字符的可见证据描述"
}
```

固定枚举：

- `direct_evidence`：`EXTERNAL_PHOTO_CARRIER`、`PHOTO_VIEWER_CONTAINER`、`NESTED_PHOTO_BOUNDARY`、`PRINTED_PHOTO_CARRIER`。
- `supporting_evidence`：`CROSS_REGION_SAMPLING`、`OUTER_PLANE_OPTICS`、`DISPLAY_EDGE_OR_CROP`。
- `weak_evidence`：`BLUR_OR_EXPOSURE`、`WATERMARK_PRESENT`、`LOCAL_MOIRE`、`PRODUCT_SCREEN_PATTERN`、`PRINT_OR_BARCODE_PATTERN`、`LOCAL_REFLECTION`、`BORDER_OR_CROP`、`STAGED_APPEARANCE`。
- `affected_non_screen_regions`：`product_body`、`package`、`hand`、`background`、`watermark`。

无对应证据时数组必须为 `[]`。最终 `result` 必须能够根据证据数组和决策顺序机械推导。

## 8. 独立部署边界

Agent 作为独立测试资产创建：

- 不修改 `tools/run_guobu_model_audit_v2.py`；
- 不并入 `COMPLIANCE_PROMPT`；
- 不修改现有生产原因码、阈值或审核结果；
- 不调用其他订单或历史图片；
- 允许后续单独替换提示词版本并保存测试结果。

Agent 文件名称、运行入口和测试脚本在后续实施计划中确定，但必须保持提示词、测试运行器和结果统计彼此独立。

## 9. 验收指标

至少分别报告：

- 非实拍高风险命中率；
- 非实拍人工复核命中率；
- 非实拍总拦截率；
- 实拍高风险误杀率；
- 实拍人工复核率；
- JSON 合法率；
- 证据代码与最终结论一致率；
- 模型异常或图片无法读取的数量。

任何模型异常、输出无法解析或图片无法读取都不得记为 `no_evidence`，测试报告中必须单独列为执行失败。

## 10. 后续迭代原则

- 每次修改只针对已确认的漏检、误杀或输出不一致问题；
- 新增规则必须说明对应的可见视觉证据；
- 不将具体样本编号、文件名、商户、水印内容或样本数量写入提示词；
- 保留每个提示词版本及其完整测试结果；
- 提示词达到可接受的召回和误杀表现后，再单独设计并入主审核链路的方案。

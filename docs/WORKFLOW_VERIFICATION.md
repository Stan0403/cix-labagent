# 工作流验证记录（2026-10-30）

## 修改基础

- 源码从 O6 当前运行的 `/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo` 取回。
- 源码基线备份：`LabAgent-backups/workflow-base-20261030T141055Z`。每次增量部署另外备份修改文件和 SQLite 数据库。
- 以文件 SHA-256 检查并发修改，只上传本轮变化的文件。
- 保留已有 Qwen3-VL-4B Q4_K_M llama.cpp 后端、权重、原有器材检测及读数接口；未运行旧版 MNN 的激活脚本。

## 软件验证

- O6 Python 回归：165 项，0 失败、0 错误、0 跳过。
- O6 Chromium 独立演练：器材最终确认后，仪表设置、表笔接触、连续读数、保存、收尾、报告自动完成。
- 各视觉步骤先返回未知再通过，确认未知阻止推进、下一轮可以恢复。
- 两次不同浏览器摄像头画面参与稳定性检查。
- 浏览器无 JavaScript 异常、无外部网页请求，390 像素布局无横向溢出。
- 此项使用明确的工具测试响应和浏览器测试摄像头，数据始终标记 `replay`，没有写入真实实验数据，也不证明模型识别准确度。

## 真实模型接口

通过 8031 端口的 `qwen3vl_gguf_service`，调用 O6 已有 8032 llama.cpp 视觉引擎；后端名称 `qwen3-vl-4b-q4_k_m-llamacpp-cpu`。

使用此前保存的同一张实拍图，三个新增规则均返回 HTTP 200 和结构化结果：

| 规则 | 样例返回 | 工作流含义 |
|---|---|---|
| 仪表设置 | COM、V/Ω、直流档为 true，量程返回 dc_200mv | 对本计划 ±2 V 范围不适用，程序不会自动通过 |
| 表笔接触 | 三项均 null | 不明确，提示调整镜头并复查 |
| 收尾 | probes_removed=false，meter_off=false | 未完成，保持本步 |

这验证了实际本地模型调用及未知、未满足结果的处理；没有声称这张图的量程识别正确，也没有声称实物完整流程已通过。最终需用现场镜头、实物档位和接触位置核验。

原始文件在板端 `test-results/workflow-real-model-validation.json`。软件回归、浏览器数据和截图在同目录的 `board-junit.xml`、`workflow-browser-validation.json`、`workflow-meter-check.png` 和 `workflow-completed.png`。

## 当前限制

视角必须能看清插孔文字、旋钮指向、金属尖端和电池两极。显示空白不代表仪表已关闭。稳定的两次读数仍可能共同误读，因此界面保留暂停、人工接管、修正及原始图像核对入口。

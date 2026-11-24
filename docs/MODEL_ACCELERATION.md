# O6 模型算力分配与实测记录

核对日期：2026-11-17。板卡为 Radxa Orion O6 / CIX P1，32 GB 内存，内核 `6.6.89-3-sky1`。以下结论来自这块板、当前驱动和已有模型的实测。

## 已部署的分配

| 工作 | 模型 | 正式执行位置 | 端口 |
|---|---|---|---|
| 文档拆解、对话、工具调用 | Qwen3-4B Q4_K_M | CPU，8 个 A720 大核，KleidiAI | 8081 |
| 语音识别 | SenseVoiceSmall `.cix` | CIX NPU；CPU 做前后处理 | 8011 |
| 器材观察与表笔复核 | Qwen3-VL-4B Q4_K_M + CIX vision.cix | NPU 视觉编码 + CPU 语言 | 模型 8034，适配器 8031 |
| 档位、接触、收尾和屏幕观察 | Qwen3-VL-4B Q4_K_M + FP16 mmproj | CPU，保留原有服务 | 模型 8032，适配器 8031 |
| 实验流程、检索、记录、网页 | Python / FastAPI / SQLite | CPU | 8001 |

本次已经构建并实测真实 Mali GPU 推理。当前 GGUF 可直接由 Vulkan 后端加载，无需重新量化。实测 GPU 更慢，未用于正式推理。器材识别另行使用实际 CIX NPU 视觉编码器；没有把修改后端名称当作硬件迁移。

SenseVoice 原来已经在 NPU。PP-OCRv4 已修正短文本前处理，但仍有负号被检测框裁掉，未接管正式读表。Qwen3 对话没有本次已验证的 NPU 生成方案。Qwen3-VL-4B 的官方视觉编码器已按用户要求用于正式器材检查，语言部分继续 CPU；完整业务链路实测约 29.9 秒。部署、固定分辨率和尚未解决的数值差异详见 [NPU 器材识别部署](NPU_EQUIPMENT.md)。

## 语言模型测速

固定三类请求，每类两轮；温度 0、固定种子、关闭 prompt 缓存、最大输出 96 token。按顺序运行，临时模型与正式服务使用不同端口。表内是 HTTP 完整响应耗时的两轮平均值，单位秒。

| 请求 | 原 CPU 运行时 | 新 CPU 运行时 | 全层 Vulkan GPU |
|---|---:|---:|---:|
| 简短中文请求 | 4.576 | 2.705 | 12.156 |
| 带资料问答 | 23.187 | 14.654 | 104.681 |
| 强制 `get_status` 工具调用 | 7.660 | 4.398 | 49.023 |

这三组请求的新 CPU 延迟降低约 37%–43%。耗时受输出长度、上下文、缓存和同时运行的视觉服务影响，不能作为所有任务的固定加速比例。

新 CPU 的 prompt 处理约 41–60 token/s，原 CPU 约 24–25；生成速度仍约 13–15 token/s。GPU 日志确认 37/37 层已卸载，prompt 处理约 3–5 token/s。GPU 可用与 GPU 更快是两个分别核验的结果。

测速的 `passed` 检查响应、非思考输出和工具协议，不是实验知识正确率。部署后另用真实 Agent 运行六项集成检查：中文非思考输出、状态工具、检索工具及返回、资料引用、暂停只生成待确认建议、缺少现场事实时拒绝代为确认。六项均通过，使用独立临时数据库；未写入真实实验会话。

## 视觉与 NPU OCR

VLM 使用一张已有器材照片及其派生反例。照片的万用表屏幕未亮，不能作为真实电压数值的正样本。各项一次完整调用耗时，单位秒。

| 校验 | 正式 CPU | GPU 视觉编码器 + CPU 语言 |
|---|---:|---:|
| 器材场景，含表笔局部复核 | 57.509 | 65.135 |
| 白图反例 | 42.561 | 45.248 |
| 未亮屏反例 | 3.025 | 4.828 |
| 缺少黑表笔反例 | 107.226 | 106.159 |

两种配置均通过这四项断言，GPU 混合路径总体没有速度收益。全 GPU 场景请求在 220 秒超时，后续项跳过；未用于正式服务。后续器材检查改用 NPU，读表与步骤检查保留 CPU。以上小样本不构成全面识别准确率或真实读数精度验收。

PP-OCRv4 临时服务成功打开 `/dev/aipu`，健康信息为 `CIX NPU PP-OCRv4`，单次耗时 0.062–0.116 秒。但三个合成数字 `1.34`、`-0.12`、`0.00` 全部未精确识别；白图及未亮屏输出为空。因此不把它用于当前自动记录流程。后续需要检查 OCR 前后处理并建立带符号、小数点和真实 LCD 的标注集，再决定是否接入。

同日后续诊断已找到主要预处理原因：参考识别器强制把短文字拉伸到 `32×400`。相同 NPU 权重下，两种字体共 12 个合成文本，原拉伸完整链路为 2/12 精确匹配，改为等比例缩放加右侧补边后为 11/12；完整文字区域直接送入识别器为 12/12。已在 `scripts/cix_ocr_service.py` 修正这项预处理并通过临时 HTTP 服务复测：`1.34`、`0.00` 正确，`-0.12` 仍丢负号。该例检测框已排除左侧负号，不能用提高识别置信度弥补。字典有 6625 类，与输出维度一致；不能据目前证据把这些错误归因于 NPU 或量化。正式读表仍保持 VLM，真实 LCD 精度尚待验收。

## 当前运行时与源码变更

- 独立运行时：`/home/radxa/llama.cpp/build-codex-vulkan/bin/llama-server`。
- 源码固定提交：`57819b8d4b39d893408e51520dff3d47d1ebb757`，版本 8323；原 CPU 构建目录保留。
- 编译：Release、`GGML_CPU_KLEIDIAI=ON`、`GGML_CPU_ARM_ARCH=armv9-a+i8mm+dotprod+sve`、`GGML_VULKAN=ON`。保留系统驱动，单独使用 Khronos Vulkan-Headers `v1.3.296` 编译。
- 启动入口：`scripts/start_accelerated_model.sh llm`；CPU 模式显式设置 `--device none --n-gpu-layers 0 --no-kv-offload --no-op-offload`，固定大核 `0,1,6,7,8,9,10,11`。
- 8192 上下文、8 推理线程、8 批处理线程、单路请求，关闭 Qwen3 思考，保留工具模板。新版本不传旧运行时的 reasoning 参数，避免输出残留 `</think>`。
- 用户单元 `labagent-demo-llm.service` 通过 `model-acceleration.conf` drop-in 切换运行时。原单元和原启动脚本保留。
- `qwen3vl_gguf_service.py` 用 `LABAGENT_EQUIPMENT_VLM_SERVER` 将器材请求分流到 8034，返回实际混合后端并还原原图框坐标；读表和步骤检查保留 CPU。正式配置与回退见 NPU_EQUIPMENT.md。
- `check_local_llm.py` 校验开闭思考标签，自动创建临时数据库父目录。

这块板默认枚举全部 Vulkan ICD 会崩溃。只对新运行时进程设置 `VK_ICD_FILENAMES=/etc/vulkan/icd.d/mali.json`，可识别真实 `Mali-G720-Immortalis`。没有修改全局 ICD，也没有升级内核或 GPU/NPU 驱动。

## 复核与回退

```bash
cd /home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo
systemctl --user status labagent-demo-llm.service --no-pager
curl --fail http://127.0.0.1:8081/health
curl --fail http://127.0.0.1:8001/api/health
.venv-app/bin/python scripts/check_local_llm.py --base http://127.0.0.1:8081/v1
```

模型服务只监听本机回环。集成检查会消耗实际推理时间，数据库隔离在临时目录。不要让重测速与真实实验并发，以免影响响应。

本次变更之前不存在同名 drop-in。需要回退语言服务时，仅移除本次创建的文件并重启该服务：

```bash
rm -- "$HOME/.config/systemd/user/labagent-demo-llm.service.d/model-acceleration.conf"
systemctl --user daemon-reload
systemctl --user restart labagent-demo-llm.service
curl --fail http://127.0.0.1:8081/health
```

板端审计和备份根目录为 `/home/radxa/.local/state/codex-model-accel/20261117/`：`source-backup/` 保存修改前脚本，`systemd-backup/` 保存原单元，JSON 记录构建、测速、集成检查及最终核对。回退模型启动配置不需要回退数据库。

本地对应目录为 `E:\codex_projext\cix-agent\model_acceleration\2026-11-17\`；开发板源码同步备份在本地 `snapshots/`。最后核对七项正式服务均 active、六个健康接口均 200、SenseVoice 与视觉编码器均有 NPU 设备句柄、临时测试端口无监听。未执行整机重启验收。

## 后续开发取向

优先减少 VLM 的重复整图调用，并用真实标注图评估每次改动。把器材定位等固定任务迁入 NPU，需要项目适用的检测权重、受支持算子、量化校准集和相应接口；当前未发现可直接接替完整 VLM 检查的已训练检测器。

读数任务需保留正负号、小数点、单位与量程检查，以及两张独立现场图的稳定性核验。完成 OCR 校验后，可以测试 NPU 小模型完成固定任务、VLM 处理复杂场景的分配。当前测试结果不足以直接启用这条流程。

参考：[Radxa O6 llama.cpp 文档](https://docs.radxa.com/orion/o6/app-development/artificial-intelligence/llama-cpp)、[Khronos Vulkan-Headers v1.3.296](https://github.com/KhronosGroup/Vulkan-Headers/tree/v1.3.296)、固定提交的 [llama.cpp 源码](https://github.com/ggml-org/llama.cpp/tree/57819b8d4b39d893408e51520dff3d47d1ebb757)。测速数字均来自本板原始 JSON，不取自产品宣传或通用跑分。

# YOLO26 器材识别部署

2026-11-19，开发板 `radxa@172.20.10.4`。项目目录：

`/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo`

## 调用链

网页器材观察 → Web API 8001 → 视觉适配器 8031 `/inspect`
→ YOLO 服务 8035 `/inspect` → CIX NPU 执行 YOLO26n INT8
→ CPU 解码、NMS、映射计划器材名称 → 返回结果和定位框 → 用户最终确认。

YOLO 支持万用表、电池、红表笔、黑表笔。“红黑表笔”需要两种表笔均检出才勾选；
只检出一支时返回待核对。未知器材返回待核对。置信度阈值 0.35。
网页仍要求人工核对，不自动提交测量结果。

## 连续检测更新

摄像头打开且真实实验处于器材检查步骤时，默认连续检测。每帧完成后等待 250 ms 再发送下一帧，
同一页面只保留一个在途请求；暂停检测、关闭摄像头、离开页面或切换步骤时停止。
使用 `/api/sessions/{id}/equipment-preview` 获取临时结果，不逐帧保存照片、不增加实验日志和 revision。
点击“拍照核对一次”仍能保存一次正式观察，最终确认继续沿用原流程。

计划中的“红黑表笔”是组合要求，模型始终独立识别 red_probe 与 black_probe。
页面现在将该组合要求展开成“红表笔”“黑表笔”两个勾选项，检测框分别显示各自名称和颜色；
确认时只有两支均勾选才满足组合要求。保留已有计划和会话的组合键，避免迁移实验记录。
手工修改清单后，连续检测继续更新框与建议，但保留用户修正。

前端生命周期和分项勾选测试通过；板端接口测试 4 项通过。
真实网页 API 连续测试 5 帧，每帧约 135–154 ms，均保留独立表笔类别；
测试前后会话 revision 均为 19，事件数均为 27，没有逐帧写入实验记录。
这是存量照片的端到端接口验证，实际相机更新频率受采集、编码和网络影响。

代码补丁：`scripts/live_equipment_ui.patch`。
本次备份：`.runtime/live-equipment-20261119-181419`；恢复该目录下四个原文件并重启
`labagent-demo-web.service` 即可回退界面更新，YOLO 推理服务无需重启。

读数 `/recognize`、步骤检查 `/check_step`、显示屏定位 `/locate_display`
继续使用通用 VLM。除器材路由和健康状态外，原有 10 个函数 AST 已核对一致。
本地对话和 NPU 语音健康检查正常。
旧的器材 VLM 和专用 NPU 视觉编码器已停止并取消随 target 启动，原文件保留。

## 配置和管理

配置使用 systemd 用户服务，本次不需要修改 `.env`。

`~/.config/systemd/user/labagent-demo-yolo.service` 的环境变量：

```dotenv
LABAGENT_YOLO_BACKEND=cix
LABAGENT_YOLO_SIZE=960
LABAGENT_YOLO_CONFIDENCE=0.35
LABAGENT_YOLO_MODEL=/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo/models/equipment-yolo26-v1/equipment_yolo26n_960_heads_int8_bias32_packed.cix
```

`~/.config/systemd/user/labagent-demo-vlm.service.d/yolo-equipment.conf`：

```dotenv
LABAGENT_EQUIPMENT_YOLO_SERVER=http://127.0.0.1:8035
LABAGENT_EQUIPMENT_VLM_SERVER=
```

```bash
systemctl --user status labagent-demo-yolo.service labagent-demo-vlm.service
curl http://127.0.0.1:8031/health
journalctl --user -u labagent-demo-yolo.service -n 50 --no-pager
```

健康状态应显示 `equipment_backend: yolo26n-cix-npu`。
端口继续只监听开发板本机，网页沿用原来的 SSH 转发方式打开。
以后使用 `board_runtime.py` 重新配置整个服务栈前，请同步这里的 YOLO
单元和 drop-in，避免旧配置覆盖新路由。

## 转换和验证

训练服务器目录 `/root/autodl-tmp/labagent-yolo26`。
官方 CIX SDK 25 Q3，CixBuilder 6.1.3407.2，隔离 Python 3.10 环境 `cix-sdk/py310`。
用 40 张训练图片校准，未用验证图片校准。切出两个原始输出，避免坐标与分类置信度共享输出量化范围。
切图后 CPU 解码与原始 ONNX 最大绝对误差 9.155e-05。

`cixgb` 产生 AIPU ELF 中间产物，必须再通过 SDK 的 `cixpack` 封装：

```bash
SDK=/root/autodl-tmp/labagent-yolo26/cix-sdk/py310/lib/python3.10/site-packages/AIPUBuilder
"$SDK/cixpack" equipment_yolo26n_960_heads_int8_bias32.aipu.bin equipment_yolo26n_960_heads_int8_bias32_packed.cix 0 1
```

编译选项 `--target X2_1204MP3 --op_plugin_options AIPU_32BIT_BIAS=1`。
编译仍有移位合并等警告，以实际 NPU 验证为准。
25 张验证图片逐张核验 SHA256；结果和计时在 `models/equipment-yolo26-v1/npu-validation.json`。
TP 70、FP 0、FN 6，6 个漏检均为电池。平均纯 NPU 推理 25.85 ms，
含预处理与后处理 69.37 ms，不含图片解码、采集和 HTTP。
通过真实 8031 器材路由测试原图和网页上传压缩图，两次均返回四种器材。
没有修改正在进行的用户实验，也没有写入模拟读数。

## 回退

原文件备份：
`/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo/.runtime/yolo26-20261119-175515`

需要恢复旧器材 VLM 时在开发板执行：

```bash
cd /home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo
BACKUP="$PWD/.runtime/yolo26-20261119-175515"
cp "$BACKUP/qwen3vl_gguf_service.py" scripts/qwen3vl_gguf_service.py
cp "$BACKUP/equipment-npu.conf.disabled" "$HOME/.config/systemd/user/labagent-demo-vlm.service.d/equipment-npu.conf"
rm "$HOME/.config/systemd/user/labagent-demo-vlm.service.d/yolo-equipment.conf"
systemctl --user daemon-reload
systemctl --user enable --now labagent-demo-npu-vision.service labagent-demo-equipment-vlm.service
systemctl --user restart labagent-demo-vlm.service
systemctl --user disable --now labagent-demo-yolo.service
```

后续若团队修改过视觉适配器，先比较差异，再恢复备份，避免覆盖新工作。

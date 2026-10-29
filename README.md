# 本地视频阶段标注

从 `sam_annotation_web_tool` 提取的视频阶段和征象标注工作区。视频、SQLite 数据库、标注和可选模型权重均保存在本机，网页资源随项目提供。启动和人工标注不需要原平台、共享存储、GPU 服务或互联网。

## 启动

需要 Python 3.11 或更新版本，以及 FFmpeg（包含 `ffprobe`）。

```bash
cd /root/sam_annotation_web_tool_local
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

打开 <http://127.0.0.1:8765>。默认使用 8765，避免占用原平台的 8080；可用 `--port 8766` 更换端口。

Mac 上进入复制后的项目目录，用同样的命令启动；也可以双击 `start.command`，第一次会创建虚拟环境并安装基础依赖。跨系统复制项目时不要复制 Linux 的 `.venv/`，在 Mac 上重新创建。首次安装依赖需要网络，安装完成后可以离线运行。

FFmpeg 安装示例：Mac 使用 `brew install ffmpeg`；Ubuntu 使用 `sudo apt install ffmpeg`。没有安装时，网页会提示，上传操作不会登记无效视频。

## 使用

1. 在视频库点击“导入视频”，可一次选择多个文件。服务流式保存并计算完整 SHA256，相同内容不会重复建档，改名仍会查重。
2. 点击“进入标注”。沿用原项目的视频播放器、时间轴、选段、阶段／画质／侧别、征象和病例编辑控件。
3. 点击“保存当前页面标注”或按 Ctrl/Cmd+S。支持本次工作区中的撤销、重做及已保存记录恢复，刷新后保存内容仍在。
4. 核对后点击“完成复核”。返回视频库选择抽帧率并点击“抽帧”，后台在本机提取可判读的有效观察帧。修改标注后需要重新复核、抽帧。
5. 勾选病例并点击“导出所选病例包”，生成包含原尺寸帧、视频标注和结构化报告的 ZIP。原平台 debug 的帧标注页可直接上传 ZIP，无需解压；导入病例带额外标签，临床信息只读，图像帧可正常标注。详见 [交接协议](docs/CASE_BUNDLE.md)。
6. 仅需标注文件时，视频卡片可导出单视频 JSON；编辑器导出按钮可导出该视频标注 ZIP；视频库顶部可导出全部标注。这个仅标注 ZIP 包含 JSON 和三份 UTF-8 BOM CSV，与含帧的病例交接包用途不同。

原视频不随标注导出。支持导入 MP4、MOV、M4V、WebM、MKV、AVI；网页能否播放取决于浏览器支持的封装和编码，推荐 MP4 / H.264。当前版本不自动转码。

多个页面保存同一视频时会比较已加载的标注快照，冲突返回 409，不直接覆盖其他页面的更新。新阶段覆盖旧阶段时保留两侧未覆盖部分。阶段编辑保留已有征象记录；涉及覆盖、拆分或改为无用片段时，需要重新核对关联征象。

## 本地数据

默认保存到项目下的 `data/`，不写入原平台的数据目录。

```text
data/
  annotations.sqlite3       视频、标注、病例信息、任务、操作记录
  videos/                   上传的原视频，内部使用随机文件名
  models/                   可选本机阶段模型权重
  frames/                   版本固定的本机抽帧结果
  exports/                  下载用病例交接 ZIP
  temporary/                上传和删除过程的临时文件
```

可通过参数或环境变量更换数据目录，例如 Mac 外接本地磁盘：

```bash
python run.py --data-dir /Volumes/AnnotationData/video-workspace
# 或设置 LOCAL_ANNOTATION_DATA_DIR
```

完整迁移／恢复：停止服务后复制整个数据目录，再指定该目录启动。只复制 SQLite 主文件可能漏掉 WAL 中的数据；备份时应停服复制整个目录。标注 ZIP 用于查阅和下游处理，当前没有 ZIP 导入入口。

## 可选阶段 AI

基础版本不安装 Torch，也不会加载 SAM。未配置本机模型时，人工标注可以完整使用，AI 分期按钮不可用。

```bash
.venv/bin/python -m pip install -r requirements-ai.txt
```

在数据目录的 `models/` 放入：

- `phase.pt`：原阶段模型 checkpoint；支持历史四级画质模型，以及符合原预处理指纹的 `nasal-phase.binary-quality.v2` 模型。
- `resnet18-f37072fd.pth`：与该阶段模型配套的 ResNet18 骨干权重。

项目不包含权重、原平台数据、凭据或患者视频，不会自动下载或使用随机权重代替真实模型。二级画质模型会验证骨干摘要、原特征函数、自适应镜圈实现和特征配置，指纹不匹配时拒绝运行。

可通过 `LOCAL_PHASE_CHECKPOINT` 指定阶段权重文件；骨干仍从当前数据目录的 `models/` 读取。`LOCAL_PHASE_DEVICE` 支持 `auto`、`cpu`、`mps`、`cuda`，默认自动选择可用设备。需要 CPU 排查时：

```bash
LOCAL_PHASE_DEVICE=cpu .venv/bin/python run.py
```

AI 在本机单任务队列中执行，关闭浏览器后继续运行。计算完成后先保存独立结果，点击“查看并应用”并确认，才会替换阶段标注；已存在征象标注时保留人工记录，仅供下载 AI 结果对照。应用后仍需人工复核，不会把辅助征象分数自动写成人工征象。

停止或重启服务会将未完成任务标记为中断，需要重新提交。该版本没有多进程调度器，请保持一个服务进程。Mac 的 MPS 兼容性、速度和真实权重输出仍待真机验收；MPS 失败时会明确显示任务失败，可指定 CPU 重试。

## 局域网

```bash
python run.py --host 0.0.0.0 --port 8765
```

其他设备访问 Mac 的局域网地址及端口。默认只监听本机。当前是单工作区、无账号版本，局域网内能访问服务的设备共享视频和编辑权限；用户／权限和其他部署服务可后续接入。上传校验在服务端完成，不依赖局域网 HTTP 页面中的 Web Crypto。

## 验证

GitHub 已配置 **macOS ARM64 validation**：相关代码推送后自动验证，也可在 [Actions](https://github.com/luv-kotoli/sam_annotation_web_tool_local/actions/workflows/macos.yml) 手动启动。运行范围、报告下载和需要仓库所有者处理的情况见 [macOS 托管验证](docs/MACOS_CI.md)。实际是否通过，以对应提交的云端运行结果为准。

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q local_annotation app.py run.py
```

接口测试使用独立临时数据库和 FFmpeg 生成的合成视频，不连接原平台。浏览器流程需要额外安装 Playwright 及 Chromium：

```bash
python -m pip install playwright
python -m playwright install chromium
python tests/browser_smoke.py
```

如果 Playwright 在另一 Python 环境，使用 `LOCAL_APP_PYTHON` 指定应用解释器。截图和报告输出到 `test-artifacts/browser/`。

Linux x86_64 / Python 3.13 上已验证人工标注、本地抽帧、病例导出和原平台隔离导入链路；测试入口和交接规则见 [交接协议](docs/CASE_BUNDLE.md)。未装实际推理依赖或模型权重，尚未完成真实 AI 推理、Mac 或 Safari 验收。

## 代码来源

`SOURCE_MANIFEST.json` 记录从原 UI 仓库和独立计算仓库提取的文件及原始 SHA256。本地适配涉及导航、播放来源文案、复核／导出行为和模型加载入口；参考特征函数与自适应镜圈文件保持原实现，以保留已有模型的预处理兼容性。所有运行入口均使用当前项目自己的代码和数据目录。

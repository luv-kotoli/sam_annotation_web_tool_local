# GitHub 托管 macOS 验证

工作流：**macOS ARM64 validation**。配置位于 `.github/workflows/macos.yml`，使用 GitHub 的标准 `macos-15` ARM64 运行器及 Python 3.13，每次最多运行 20 分钟，同一分支的新任务会取消未完成的旧任务。

相关代码推送到 `main` 会自动启动；仅修改说明文档不会启动，也没有定时任务。可以在 [Actions 页面](https://github.com/luv-kotoli/sam_annotation_web_tool_local/actions/workflows/macos.yml) 手动选择 **Run workflow → main → Run workflow**。

## 已完成的首次验证

2026-10-29，提交 `c752ac9` 在 GitHub 托管的 **Darwin / arm64 / Python 3.13.15** 上通过：[成功运行 #3](https://github.com/luv-kotoli/sam_annotation_web_tool_local/actions/runs/40893129142)。

- 接口、SQLite、冲突保护、复核、抽帧及 ZIP 合约测试：16 项通过，0 失败、0 错误、0 跳过。
- Chromium 标注、保存／恢复、抽帧、选例导出和窄屏检查：通过。Mac 上长服务端目录导致的窄屏横向溢出已修复。
- 硬件清单：Core ML 可见 `gpu`、`cpu`；Metal 设备名为 `Apple Paravirtual device`；`neural_engine_accessible=false`。
- 未执行真实模型、MPS 或 Neural Engine 推理，也未测目标 Mac mini 的性能。

首次任务已能自动触发、执行并上传报告，目前无需仓库所有者额外开启权限或配置凭据。

## 测试内容

1. 创建独立 Python 虚拟环境，安装基础运行依赖、Playwright 和 Chromium，准备 FFmpeg。不会安装 Torch、下载权重或读取本地样例视频。
2. 明确检查操作系统为 Darwin、架构为 arm64，并记录 Python、依赖、FFmpeg 和 Node 版本；不会把 Linux 上的结果标成 Mac 验证。
3. 检查 Python 与 JavaScript 语法，运行接口、数据库、版本冲突、复核、真实 FFmpeg 抽帧及病例 ZIP 测试。缺少 FFmpeg 导致的跳过不会算成功。
4. Chromium 自动操作批量上传、阶段／征象／病例保存、刷新持久化、撤销重做、复核、抽帧和病例 ZIP 下载，以及窄屏视频库。所有视频由 FFmpeg 临时生成。
5. 通过系统 Core ML / Metal 框架记录可见计算设备。这只是设备能力清单，不加载模型，不证明 MPS 或 Neural Engine 推理可用；此项不可用时保留日志，不阻止功能测试。

测试只使用当前仓库和临时数据，不连接原网关、wg-data 或 SAM 服务。跨仓库 `browser_transfer.py` 不属于这份云端作业。Safari、真实模型、性能和目标 Mac mini 的局域网仍分别验收。

## 查看结果

打开 Actions 中最近的运行，确认 **commit** 是要验证的提交：

- Summary 汇总系统架构、单元测试数量、浏览器结果和设备清单；缺失报告显示为未完成，不写成通过。
- 点击具体 job 可以展开失败步骤，查看原始日志。
- 环境、测试统计和设备清单也会作为任务注释发布；浏览器失败会附上断言详情，方便不下载完整日志时定位问题。
- 运行页下方的 **Artifacts → macos-arm64-validation** 可下载详细日志、JSON 报告、合成视频的页面截图、浏览器 trace 和导出的测试 ZIP，保留 7 天。下载产物可能需要登录 GitHub。
- 出错时优先查看 `unit-tests.log`、浏览器 `report.json`、`server.log`、`failure.png`；安装阶段失败则直接查看对应 Actions 步骤。

绿色功能测试表示当前代码在该托管 Mac 上通过了这些检查，不表示目标硬件的速度或 NPU 推理已验证。

## 可能需要仓库所有者操作

正常情况下，推送后无需你安装 Mac 或提交凭据；GitHub 会提供运行环境。

1. **确认首次任务运行。** 打开 Actions 页面。若显示 Actions 被禁用，进入仓库 **Settings → Actions → General** 启用，并允许工作流中的 GitHub 官方 actions。若没有自动运行，可手动点击 Run workflow。
2. **如因额度／组织策略无法排队。** 查看任务页给出的原因，再检查账户的 Actions 使用额度、预算或组织策略。公开仓库的标准 GitHub 托管运行器目前免费；私有仓库和付费运行器按 GitHub 的当前规则处理。这份配置没有申请付费的大型运行器。
3. **如需要人工协助排错。** 提供运行页面链接、失败步骤日志或下载的报告即可，不需要发送密码、令牌、患者视频或模型权重。

仓库推送权限与 GitHub Actions API 权限是两回事。如果执行环境只有 SSH 推送权限，仍可由推送触发任务；公开运行状态通常能查询，私有日志／产物可能需要由已登录的仓库所有者查看。

## 在开发机复查

安装 FFmpeg、Node 和 `requirements-ci.txt` 后运行：

```bash
python scripts/ci_validate.py
python -m playwright install chromium
python tests/browser_smoke.py
python scripts/ci_summary.py
```

只有实际 Mac ARM64 才能使用 `--require-macos-arm64`。测试报告和截图位于被 Git 忽略的 `test-artifacts/`。

参考：[运行器规格](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)、[手动运行](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)、[计费规则](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。

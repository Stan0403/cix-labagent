# 无插件发布 GitHub

当前 `Stan0403/cix-labagent` 已由所有者于 2026-11-25 改为公开。浏览、克隆和直接下载 Release 附件无需登录。以下 CLI 登录和默认私有参数用于有写入权限的发布者创建或推送仓库，不是访问当前公开仓库的前提。

GitHub CLI 官方文档：https://cli.github.com/manual/gh_repo_create 。本流程不使用 Codex 插件，也不要求把个人令牌贴进聊天。

先安装 Git 和 GitHub CLI，运行 `gh auth login --hostname github.com --web`，在 GitHub 官方网页完成登录授权。检查 `gh auth status`。

PowerShell 在源码根目录运行 `./deploy/publish_github.ps1 -Name cix-labagent`。脚本使用已登录账号，以 `id+login@users.noreply.github.com` 作为本仓库提交身份；不更改全局 Git 身份。默认私有，用户明确需要公开才传 `-Visibility public`。

也可手动执行：

```bash
git init -b main
git add .
git commit -m "Deliver current CIX LabAgent source, reproducible deployment and whitepaper"
gh repo create cix-labagent --private --source . --remote origin --push
gh repo view --json url --jq .url
```

手动提交需要先配置自己的 Git 作者身份。如果该名字已存在，使用其他名字，或核对其 remote 后再推送，避免误改既有仓库。

推送完成后可上传 PDF、源码 ZIP、OCR 模型 ZIP 作为 Release 附件。下列路径必须换成实际附件位置，源码 ZIP 本身不应提交到源码仓库：

```bash
gh release create v0.1.0-delivery /path/to/cix-labagent-source.zip /path/to/lcd-reader-v3.zip \
  docs/CIX-LabAgent-Technical-Whitepaper.pdf --title "CIX LabAgent delivery 2026-11-24" \
  --notes "Current local source snapshot, reproducible web deployment, O6 artifact-gated installer, and evidence-based whitepaper."
```

如果完整模型包超过单个 Release 附件限制，先按目录拆分，分别保留锁定清单。Git 仓库不包含个人密钥、`.env`、正式数据库或拍摄照片。发布后将真实 URL 加入 README，并在新电脑按该提交克隆、安装和测试；尚未运行的 GitHub CI 不能描述成已通过。

# 发布维护 / Release maintenance

[中文](#中文) · [English](#english)

## 中文

### 为什么 README 比 Releases 新

`main` 上的 README、SKILL 和 CHANGELOG 描述当前源码版本；GitHub Releases 记录单独发布的标签快照。修改版本文字、合并 PR 或通过 CI 都不会自动创建 Release。2026-10-26 补发前核查时，文档已到 v0.3.3，但最新正式发布及标签仍为 v0.2.0；此前仅有检查工作流，没有发布工作流。v0.3.3 现已正式发布。

### 当前桌面正式版 v0.13.1

v0.13.1 在完整双平台验收后按正式 Release 发布并设置 Latest。后续通过验收的常规修复默认正式发布；需要公开试用的版本应单独注明 Pre-release。使用 [v0.13.1 双语说明](releases/v0.13.1.md)，运行 `python3 scripts/prepare_release.py v0.13.1`，将新标签绑定到已检查的源码提交。本次公开附件为以下三份程序包和一份合并清单：

- `BioReader-v0.13.1-macos-arm64.dmg`
- `BioReader-v0.13.1-windows-x64-setup.exe`
- `BioReader-v0.13.1-windows-x64.zip`
- `SHA256SUMS.txt`

两平台桌面外壳使用 Go + MyGo v0.7.4，现有 Python 阅读服务和 PDF 依赖作为 `BioReaderService` 随包分发。用户不需安装 Python。默认资料目录保持不变：macOS 为 `~/Library/Application Support/BioReader/`，Windows 为 `%LOCALAPPDATA%\BioReader`。同步发布不表示应用内自动更新或云端文库同步。

#### 构建与产物核对

在同一个已检查、已提交的干净源码版本上构建 macOS 与 Windows。原生构建入口为 [`app/build-macos.sh`](app/build-macos.sh) 和 [`app/build-windows.ps1`](app/build-windows.ps1)，读取 `SKILL.md` 的版本。构建工具需要 Go 1.27.3+、目标平台的 64 位 Python 3.10+ 和依赖下载网络；脚本创建独立 Python 构建环境。详细参数见 [MyGo 开发说明](app/mygo/README.md)及[桌面构建说明](app/DESKTOP.md)。

已有依赖时可直接运行 `python scripts/build_mygo.py --version v0.13.1 --python /path/to/build-python`。`--skip-backend` 只用于开发，会标记 dirty，不能通过最终发布验收；`--skip-installer` 不会产生本次要求的 Windows 安装 EXE。

Mac 构建结果在 `app/mygo/build/darwin-arm64/`。在仓库根目录执行：

```bash
app/.desktop-venv/bin/python scripts/package_mygo.py --version v0.13.1 --commit "$(git rev-parse HEAD)"
```

Windows x64 构建结果在 `app/mygo/build/windows-amd64/`。在一次性的 Windows 测试环境或 CI runner 中执行安装／卸载验收：

```powershell
$sourceCommit = git rev-parse HEAD
.\app\.desktop-venv\Scripts\python.exe scripts/package_mygo.py --version v0.13.1 --commit $sourceCommit --test-installer
```

[`scripts/package_mygo.py`](scripts/package_mygo.py) 检查实际解压 ZIP 或挂载 DMG 内的前端字节、运行时报告中的源码提交／版本／dirty 标记，并运行随包原生诊断。Mac 还检查 DMG 和 App 签名完整性；这不等于 Apple 开发者签名或公证。Windows 的 `--test-installer` 额外运行静默安装、已安装程序诊断及卸载。正常便携包必须包含 `BioReader/BioReader.exe`、同级 `BioReaderService/` 及全部随附文件；不要沿用旧版只核对顶层 `_internal` 的方式。

输出保存在 `app/dist/mygo-release/`，含该平台程序附件、`SHA256SUMS.txt`、`build-info.json` 和诊断 JSON。打包命令负责验收与生成文件，不创建 GitHub Release。[Windows 工作流](.github/workflows/windows-build.yml) 也只生成工件；下载前核对运行提交及实际检查结果。

#### GitHub 原生构建与草稿附件

将同一已检查提交合并至 `main`，创建指向准确源码 SHA 的新标签和一个现有的草稿 Release。运行 [桌面预览工作流](.github/workflows/desktop-preview.yml)，传入 `version`、完整的 `source_commit` 与数字 `release_id`。工作流只从 `main` 调度，核对草稿、标签与源码后，在 macOS arm64 / Windows x64 原生环境分别构建并运行测试；macOS 固定使用 uv 管理的独立 CPython 3.12.11，避免框架 Python 与侧车签名布局冲突。

构建任务先保存程序包、清单和诊断到 Actions artifacts。单独上传任务根据 Git 树核对前端文件数量、源码／版本、干净状态、原生 GUI 检查与程序摘要；Windows 还要求安装和卸载验证通过。它仅将三份程序附件上传到已存在的草稿：同名同摘要跳过，不同摘要拒绝覆盖。上传失败可重跑失败任务，继续使用同一次运行已保留的构建产物。工作流不发布 Release，不设置 Latest，也不替代最终合并的 `SHA256SUMS.txt`。`upload-preview-assets.yml` 仅保留为 v0.10.0 历史恢复流程，后续版本使用本工作流。

#### 诊断与公开发布

`BioReader --self-test /absolute/path/report.json --gui-test` 使用隔离临时资料，启动实际随包服务，检查会话限制、PDF／笔记持久化与子进程关闭，并在使用合成数据的可见 WKWebView／WebView2 窗口中用 PDF.js 渲染合成 PDF。它覆盖这段原生自动化路径；系统保存对话框、外部浏览器、剪贴板和各 Windows 用户环境仍须按实际交互验证记录描述。没有 `--gui-test` 的检查只覆盖服务路径，不能写成原生窗口验收。不可在最终原生 CI 完成前预填“全部通过”，也不将合成材料和模拟模型测试称为科学阅读质量评测。

合并两平台生成的 SHA-256 清单，逐项核对上述三个程序附件的文件名、字节数和摘要。`build-info.json`、诊断报告及 Actions 结果留作构建证据；它们不替代最终合并的 `SHA256SUMS.txt`。公开 Release 前保持草稿，等三份程序附件、合并清单和对应平台验证齐备，再以正式版发布（`--prerelease=false --latest=true`）。发布后核对标签提交、三份程序附件和清单、正式发布标记及当前版本的 Latest 状态；不覆盖旧标签。

本版修复推理模型的读图与长文整理。验收须覆盖非流式完成状态分类、截断结果不保存、预算重试次数、已有笔记保护、分段任务取消，以及智谱官方指定模型的适配范围；其他端点和模型不得被自动增加不支持的参数。保留外观的字号独立、浅深色及系统变化、偏好持久化、语言并发与保存失败恢复回归。验收需覆盖缩略图与「查看笔记」入口、左右／上下布局、图像缩放、完整 Markdown 与空态，并确认打开已有观察不调用模型、不重新生成内容、不修改正文笔记。核对 HTML、危险链接、远程图片和内部伪引用的渲染边界，以及当前文献的图像来源。回归、浏览器、原生自动化和人工交互结果分别记录在 [App 验证文件](app/tests/README.md)；没有实际完成的检查不要写成通过。

### 桌面预览版 v0.9.0（历史）

v0.9.0 的 Python 桌面外壳以 macOS arm64 ZIP 和 Windows x64 ZIP 发布，说明与附件保留在 [v0.9.0 发布说明](releases/v0.9.0.md)。其程序目录结构和后端自检范围不能代替 v0.10.0 的 MyGo、DMG、安装 EXE 与原生窗口诊断要求。

### 桌面预览版 v0.4.0（历史）

v0.4.0 的桌面包按 GitHub **Pre-release** 发布，稳定版 v0.3.4 保留。当前 `Publish release` 工作流只创建正式版本并设置 Latest，不能用于这次桌面预览发布。

准备好[双语发布说明](releases/v0.4.0.md)和一致的版本资料后，执行 `python3 scripts/prepare_release.py v0.4.0` 及下述回归检查，将文档合并到 `main`。在 GitHub Releases 手动创建指向该次已检查提交的新标签 `v0.4.0`，勾选 **Set as a pre-release**，使用已验证的发布说明，附上 `BioReader-v0.4.0-macos-arm64.zip` 和 `SHA256SUMS.txt`；发布后核对标签提交、预发布标记和两份附件。不要运行稳定版工作流，也不要覆盖已有标签或 Release。

ZIP 应保留 `BioReader.app` 的目录、权限和符号链接，解压后复核应用包完整性与 arm64 可执行文件。预览包不包含用户数据、API Key 或虚拟环境；说明须保留未做 Apple 开发者签名／公证以及尚未完成 GUI 验收的限制。发布前可以保留草稿，附件上传和核验完成后再公开。

### 发布稳定版本

以下以 v0.3.4 为例；后续发布应替换为准备好的新版本号，不能重复发布已有版本。

1. 审阅 [v0.3.4 中英文发布说明](releases/v0.3.4.md)，确认待发布改动、版本资料与验证记录一致，并将对应 PR 合并到 `main`。
2. 打开仓库 **Actions → Publish release → Run workflow**，选择 **main**，输入版本 **v0.3.4**。也可以在仓库目录运行：

   ```bash
   gh workflow run release.yml --ref main -f version=v0.3.4
   ```

3. 检查该次运行是否成功，再到 [Releases](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases) 确认 v0.3.4 和 Latest 标记。标签绑定触发运行时的 `main` 提交；运行期间的新提交不包含在该版本中。

合并 PR 本身不执行发布；其他分支上的运行会跳过发布任务。工作流只接受 `vX.Y.Z` 稳定版本，先检查 README 中英文、SKILL、CHANGELOG 与发布说明一致，再运行仓库完整性和记录回归检查，最后创建标签及正式 Release。新发布使用新的稳定版本号，不覆盖已有发布，也不补造历史发布日期。

### 后续版本

- 在同一个 PR 中更新 `SKILL.md` 版本声明、README 中英文版本和 CHANGELOG 的最新正式版本章节，并添加 `releases/vX.Y.Z.md`。发布说明以 `# vX.Y.Z` 开头，包含 `## 中文` 与 `## English`，如实记录验证范围；比较基准使用上一次实际发布的版本。
- 发布说明中的仓库文件链接使用带目标标签的完整 GitHub URL；不要使用在 Release 页面无法定位的相对文件链接。
- 合并前执行以下检查（将示例版本替换为目标版本）；版本验证脚本只读取本地资料并输出发布说明，不联网、不创建标签。

  ```bash
  python3 scripts/prepare_release.py v0.3.4 --output /tmp/biomedical-release-notes.md
  python3 evals/test_prepare_release.py -v
  python3 evals/validate_repo.py
  python3 evals/test_reading_record.py -v
  ```

- 合并后按上述步骤手动发布。工作流使用 GitHub 提供的 `GITHUB_TOKEN` 和 `contents: write` 权限；通常无需另建个人令牌。若仓库规则禁止 Actions 创建标签或 Release，运行会失败并保留日志，需要维护者处理相应权限。

### 自动维护 GitHub Actions

[Dependabot 配置](.github/dependabot.yml)合并到默认分支后，GitHub 将按每周一北京时间 09:00 的计划检查工作流引用的 Action，发现新版后自动创建更新 PR，由现有 CI 检查，维护者审阅后合并。Action 继续使用完整提交 SHA 固定，并在行尾标注对应版本。这项服务在 GitHub 上运行，不需要 Codex 持续在线，也不会自动合并 PR 或发布新版本。

此配置只维护 Action 引用；`runs-on` 的 Ubuntu 镜像和 `python-version` 不由它升级。CI 与发布任务固定使用 `ubuntu-24.04`，运行环境升级需另开 PR 验证。已发布的 v0.3.3 保留原标签快照，这类工作流维护不需要重复发布同一版本。

### 重试与现有标签

已有 Release（包括草稿）不会被覆盖；已有标签仅在其最终指向的提交与此次检查的提交相同时允许继续，注释标签同样适用。若创建标签后发布失败，使用原运行的 **Re-run failed jobs**，以保留同一个提交。若从新的 `main` 提交另起运行而旧标签已经存在，工作流会拒绝目标不一致；不要移动已发布标签来规避此检查，应核实原运行或准备新版本。所有发布运行串行执行。

## English

### Why the README can be newer than Releases

README, SKILL, and CHANGELOG on `main` describe the current source version; GitHub Releases are separately published tag snapshots. Editing version text, merging a PR, or passing CI does not create a Release. Before the catch-up release on 2026-10-26, the documentation was at v0.3.3 while the latest release and tag were still v0.2.0. The repository previously had a checking workflow but no publishing workflow. v0.3.3 has since been published.

### Current desktop release v0.13.1

Publish v0.13.1 as a regular Release and Latest after complete native validation on both platforms. Subsequent validated maintenance releases use this policy; explicitly identify experimental previews separately. Use the [v0.13.1 bilingual notes](releases/v0.13.1.md), run `python3 scripts/prepare_release.py v0.13.1`, and bind the new tag to the checked source commit. Publish these three application packages and one combined checksum list:

- `BioReader-v0.13.1-macos-arm64.dmg`
- `BioReader-v0.13.1-windows-x64-setup.exe`
- `BioReader-v0.13.1-windows-x64.zip`
- `SHA256SUMS.txt`

Both desktop shells use Go + MyGo v0.7.4. The existing Python reading backend and PDF dependencies ship inside `BioReaderService`; users do not install Python. Default library locations remain `~/Library/Application Support/BioReader/` on macOS and `%LOCALAPPDATA%\BioReader` on Windows. Synchronized publication does not provide an in-app updater or cloud library synchronization.

#### Build and verify the artifacts

Build macOS and Windows from the same checked, clean, committed source revision. Native build entry points are [`app/build-macos.sh`](app/build-macos.sh) and [`app/build-windows.ps1`](app/build-windows.ps1); both read the version from `SKILL.md`. Build tools require Go 1.27.3+, native 64-bit Python 3.10+, and network access for dependencies. The scripts create an isolated Python build environment. See the [MyGo developer guide](app/mygo/README.md) and [desktop build guide](app/DESKTOP.md).

With dependencies already installed, run `python scripts/build_mygo.py --version v0.13.1 --python /path/to/build-python`. The development-only `--skip-backend` option marks the build dirty and cannot pass final release validation. `--skip-installer` omits the Windows setup executable required for this release.

The Mac build is written to `app/mygo/build/darwin-arm64/`. From the repository root, run:

```bash
app/.desktop-venv/bin/python scripts/package_mygo.py --version v0.13.1 --commit "$(git rev-parse HEAD)"
```

The Windows x64 build is written to `app/mygo/build/windows-amd64/`. Test installation and removal only on a disposable Windows environment or CI runner:

```powershell
$sourceCommit = git rev-parse HEAD
.\app\.desktop-venv\Scripts\python.exe scripts/package_mygo.py --version v0.13.1 --commit $sourceCommit --test-installer
```

[`scripts/package_mygo.py`](scripts/package_mygo.py) checks frontend bytes inside the actual extracted ZIP or mounted DMG, verifies the runtime's commit/version/dirty metadata, and runs the packaged native diagnostic. Mac validation also checks DMG and application-signature integrity; these checks do not provide Apple Developer ID signing or notarization. On Windows, `--test-installer` also performs a silent install, checks the installed executable, and runs its uninstaller. The portable ZIP must contain `BioReader/BioReader.exe`, the sibling `BioReaderService/` directory, and every accompanying file. The old layout's top-level `_internal` check is no longer sufficient.

Outputs go to `app/dist/mygo-release/`: platform application assets, `SHA256SUMS.txt`, `build-info.json`, and diagnostic JSON. Packaging generates and checks files without creating a GitHub Release. The [Windows workflow](.github/workflows/windows-build.yml) also produces artifacts only; verify the run's source commit and actual check results before downloading them.

#### Native GitHub builds and draft assets

Merge the checked source into `main`, create a new tag at the exact source SHA, and create one draft release. Run the [desktop preview workflow](.github/workflows/desktop-preview.yml) from `main` with `version`, the full `source_commit`, and numeric `release_id`. It validates the existing draft and tag, then builds and tests on native macOS arm64 and Windows x64 runners. macOS uses uv-managed standalone CPython 3.12.11 to avoid framework-Python signing layout conflicts.

Build jobs first preserve packages, manifests and diagnostics as Actions artifacts. Separate upload jobs verify the frontend count against the Git tree, source/version, clean build, native GUI diagnostics and artifact hashes; Windows also requires installation and removal checks. They upload application assets only to the existing draft, skip an identical asset and refuse to overwrite a different digest. Failed upload jobs can reuse artifacts from the same run. This workflow never publishes the Release, changes Latest or replaces the final combined `SHA256SUMS.txt`. The old `upload-preview-assets.yml` remains a historical v0.10.0 recovery workflow.

#### Diagnostics and publication

`BioReader --self-test /absolute/path/report.json --gui-test` uses temporary isolated storage, starts the actual bundled service, checks its session boundary, PDF/note persistence, and child shutdown, and renders a synthetic PDF through PDF.js inside a visible native WKWebView/WebView2 window with synthetic content. This covers that automated native path. System save dialogs, external browsers, clipboard behavior, and users' Windows environments need separate acceptance records. Without `--gui-test`, the diagnostic covers only the service path. Do not predeclare success before final native CI completes, and do not describe synthetic fixtures or simulated models as scientific reading-quality evaluation.

Combine both platforms' SHA-256 lists and verify each of the three application assets by filename, byte size, and digest. Retain `build-info.json`, diagnostic reports, and Actions results as build evidence; they do not replace the final combined `SHA256SUMS.txt`. Keep the Release in draft until all three application assets, the merged hash list, and platform validation are ready. Publish with `--prerelease=false --latest=true`, then verify the tag commit, assets, regular-release flag and the current version's Latest status. Do not overwrite an existing tag.

This version fixes figure analysis and long-document condensation with reasoning models. Validate non-streaming completion classification, rejection of truncated results, bounded retries, existing-note preservation, task cancellation, and the precise scope of the official BigModel adaptation. Other endpoints and models must not receive unsupported parameters. Retain regressions for independent sizing, live system changes, preference persistence, concurrent locale saves and failure recovery. Acceptance should cover thumbnail and View notes entry points, side-by-side and stacked layouts, image zoom, complete Markdown and empty states. Confirm that opening saved observations does not call a model, regenerate content or modify paper-level notes. Check HTML, unsafe links, remote images and internal pseudo-link boundaries, and verify that the displayed image belongs to the current paper. Record regression, browser, native automated and manual results separately in the [App validation log](app/tests/README.md); do not mark unperformed checks as passed.

### Desktop preview v0.9.0 (historical)

v0.9.0 shipped the Python desktop shell as macOS arm64 and Windows x64 ZIPs. Its notes and assets remain documented in the [v0.9.0 release notes](releases/v0.9.0.md). Its directory layout and backend diagnostic scope do not replace the MyGo, DMG, setup executable, and native-window validation requirements of v0.10.0.

### Desktop preview v0.4.0 (historical)

The v0.4.0 desktop bundle is a GitHub **Pre-release**; the stable v0.3.4 remains available. The current `Publish release` workflow creates a stable release and marks it Latest, so it must not be used for this desktop preview.

Prepare the [bilingual notes](releases/v0.4.0.md) and matching version metadata, run `python3 scripts/prepare_release.py v0.4.0` and the regression checks below, and merge the documentation into `main`. In GitHub Releases, manually create a new `v0.4.0` tag pointing to that checked commit, select **Set as a pre-release**, use the validated notes, and attach `BioReader-v0.4.0-macos-arm64.zip` and `SHA256SUMS.txt`. After publication, verify the tag commit, pre-release flag, and both assets. Do not run the stable-release workflow or overwrite an existing tag or Release.

The ZIP must preserve the `BioReader.app` directory, permissions, and symlinks. After extraction, recheck bundle integrity and the arm64 executable. Exclude user data, API keys, and virtual environments, and retain the notes about absent Apple Developer ID signing/notarization and outstanding GUI acceptance tests. A draft can hold the assets until upload and verification are complete.

### Publish a stable version

The following example uses v0.3.4. For future releases, substitute the prepared new version; existing versions cannot be published again.

1. Review the [bilingual v0.3.4 release notes](releases/v0.3.4.md), confirm that changes, version metadata, and validation records agree, and merge the corresponding PR into `main`.
2. Open **Actions → Publish release → Run workflow**, select **main**, and enter **v0.3.4**. Alternatively, run this from the repository:

   ```bash
   gh workflow run release.yml --ref main -f version=v0.3.4
   ```

3. Confirm that the run succeeds and that [Releases](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases) shows v0.3.4 as Latest. The tag points to the `main` commit captured when the run was triggered, excluding commits added during the run.

Merging the PR does not publish a release. Runs on other branches skip the publishing job. The workflow accepts stable `vX.Y.Z` versions only, checks both README versions, SKILL, CHANGELOG, and release notes, then runs repository integrity and reading-record regression checks before creating the tag and public Release. New releases use new stable version numbers; they do not overwrite existing releases or invent historical publication dates.

### Future versions

- In one PR, update the version declaration in `SKILL.md`, both README version declarations, and the latest numbered CHANGELOG section, and add `releases/vX.Y.Z.md`. Notes must start with `# vX.Y.Z`, contain `## 中文` and `## English`, accurately describe validation coverage, and compare against the last actually published release.
- Use full GitHub URLs containing the target tag for repository files linked in release notes; relative file links may not resolve on the Release page.
- Before merging, run the four commands in the Chinese section above, replacing the example version. The version checker only reads local files and outputs notes; it does not access the network or create tags.
- After merging, publish manually as described above. The workflow uses GitHub's `GITHUB_TOKEN` with `contents: write`; a separate personal token is normally unnecessary. Repository rules that disallow tag or Release creation by Actions will cause the run to fail with logs for maintainers to investigate.

### Automatic GitHub Actions maintenance

Once the [Dependabot configuration](.github/dependabot.yml) is merged into the default branch, GitHub checks workflow Action references on a weekly schedule, Mondays at 09:00 Asia/Shanghai. It opens update PRs when newer versions are available; the existing CI checks them, and maintainers review and merge them. Actions remain pinned to full commit SHAs with corresponding version comments. This service runs on GitHub and does not require Codex to remain online. It does not automatically merge PRs or publish releases.

This configuration updates Action references only, not the Ubuntu image in `runs-on` or `python-version`. CI and release jobs use `ubuntu-24.04`; runtime upgrades require a separate PR and validation. The published v0.3.3 tag remains its original snapshot; workflow maintenance does not require publishing the same version again.

### Retries and existing tags

Existing Releases, including drafts, are never overwritten. An existing tag is accepted only if it resolves to the same commit that this run checked; annotated tags are supported. If publication fails after tag creation, use **Re-run failed jobs** on the original run to preserve its commit. A new run from a newer `main` commit will reject an existing tag pointing elsewhere. Do not move published tags to bypass this check; inspect the original run or prepare a new version. Release runs are serialized.

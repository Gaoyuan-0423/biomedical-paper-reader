# BioReader macOS / Windows 桌面版

v0.13.1 桌面版使用 Go + [MyGo v0.7.4](mygo/README.md) 承载阅读界面：macOS 使用系统 WKWebView，Windows 使用 WebView2。现有 Python 阅读服务、解释器和 PDF 依赖随包提供，用户无需安装 Python。关闭窗口前会等待笔记和阅读位置保存；关闭应用后停止随附服务。PDF、笔记和设置保存在用户数据目录，与程序文件分开：

| 系统 | 默认数据目录 |
|---|---|
| macOS | `~/Library/Application Support/BioReader/` |
| Windows | `%LOCALAPPDATA%\BioReader` |

设置 `BIOREADER_DATA_DIR` 可使用自定义目录。更新前退出 BioReader，再替换程序文件，已有论文、笔记和设置继续保留。源码网页版的 `app/data/` 不会自动迁移；需要迁移时先备份，再复制其中的内容到对应用户数据目录。

## 外观与字号（自 v0.13.0）

打开「设置 → 外观与字号」，可独立调整界面字号（12–18 px）与笔记字号（12–24 px），选择蓝、青或紫色配色，以及「跟随系统／浅色／深色」。系统模式随操作系统的浅深色变化即时切换；字号由应用中的滑块独立控制。设置即时预览、自动保存，重启后恢复；保存失败可重试。

笔记字号用于正文预览、编辑器、读图笔记和问答内容。界面配色不会反色 PDF 或原图，也不改变导出排版、研究内容与编辑草稿。系统窗口标题栏及文件选择器继续遵循操作系统外观。

## 读图笔记查看器（自 v0.12.0）

在「图库」点击缩略图或「查看笔记」，左侧显示图片，右侧以 Markdown 显示该图完整的已保存读图观察，并可展开原图注。图像可适应窗口、放大或缩小；窄窗口将图像与笔记上下排列。已有结果直接可看，无需重新读图、重新提取或重新生成论文笔记；没有保存结果时给出明确提示，仍由用户回图库主动发起读图。

查看器只读保存的图级观察，不改写独立的论文正文笔记，也不自动调用模型。右侧不执行 HTML、不加载 Markdown 内嵌图片或激活内部伪引用；左侧使用当前文献的可信本地图资源。普通安全 HTTP／HTTPS 外链可用。详细操作见 [App 说明](README.md#查看已有读图笔记自-v0120)。

本版浏览器、原生窗口和产物验收分别记录在 [App 验证记录](tests/README.md)，以实际完成结果为准。

## 补充图图注更新（自 v0.11.1）

v0.11.1 修复补充图标题与正文字号差异造成的图注截断，并保守连接版面连续的跨页图注。自 v0.12.1 起，打开已有图库会自动升级可识别的旧版自动短图注，并保留人工修改。如需主动重提，可在左侧 PDF 文件下拉框选择对应补充材料，再在「图库」点击「重新提取」。更新图注不需要重新导入 PDF 或重新生成笔记。

同一文件重提会补全未人工编辑的自动图注，保留人工图注、已有读图观察、手动裁图与原有图片 ID；已插入笔记的图片继续引用原图。该修复不改图号标签、页码或裁剪区域，也不自动重写已有笔记和模型观察。提取结果仍应对照原文，尤其是扫描件和无法确认连续性的跨页内容。

工程回归、实际材料的隔离提取检查及跨平台构建结果见 [App 验证记录](tests/README.md)；最终原生产物验收以同版 Release 构建记录为准。

## 界面语言与新图标

顶部或设置中的「界面语言」支持「简体中文」和「English」，切换立即更新当前界面，保留笔记和表单草稿、输入焦点、PDF 选区及活动任务。语言偏好保存在用户数据目录的 `ui-preferences.json`，不依赖桌面服务每次启动时使用的端口；重新打开应用后沿用已保存的语言。保存失败会提示重试。已有论文、笔记与观察保持原文；新生成的精读、速读、读图和问答使用任务启动时的界面语言。

书页与叶片图标统一用于界面品牌、浏览器图标及桌面应用。Go + MyGo 的下载、退出与错误提示提供中英文；原生文件选择器的系统按钮仍跟随操作系统语言。底层桌面架构和现有用户数据目录保持不变。

界面语言、图注提取与读图查看器的验证分别记录。最终跨平台原生构建、安装及打包验收结果以同版 Release 的构建记录及 [App 验证记录](tests/README.md) 为准；浏览器检查不代替系统原生对话框验收。

## 下载与安装

下载入口：[v0.13.1 桌面版](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases/tag/v0.13.1)，附件以发布页实际提供为准。v0.13.1 验收通过后作为正式 Release 和 Latest 发布。本次发布三种程序附件及一份合并校验清单：

| 平台与用途 | 文件 |
|---|---|
| Apple 芯片 Mac（arm64） | `BioReader-v0.13.1-macos-arm64.dmg` |
| Windows 10 / 11（x64）安装向导 | `BioReader-v0.13.1-windows-x64-setup.exe` |
| Windows 10 / 11（x64）便携程序 | `BioReader-v0.13.1-windows-x64.zip` |
| 三份程序附件的 SHA-256 | `SHA256SUMS.txt` |

没有 Intel Mac、Windows ARM64 原生包或 Linux 安装包。桌面包不需要手动启动浏览器／终端。

### macOS

1. 下载 [BioReader-v0.13.1-macos-arm64.dmg](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases/download/v0.13.1/BioReader-v0.13.1-macos-arm64.dmg)。
2. 打开 DMG，将 `BioReader.app` 拖入“应用程序”。更新旧版前先退出正在运行的 BioReader，再替换 App；复制完成后可推出磁盘映像。
3. 从“应用程序”双击打开 BioReader。AI 笔记需要在设置中配置模型接口和 API Key。

macOS 包未进行 Apple 开发者签名和公证。macOS 可能阻止首次打开；包完整性检查不代表 Apple 公证或所有 Mac 的安装验收。

### Windows

1. 安装版下载 [BioReader-v0.13.1-windows-x64-setup.exe](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases/download/v0.13.1/BioReader-v0.13.1-windows-x64-setup.exe)，退出旧版后运行安装向导。
2. 如需便携程序，下载 [BioReader-v0.13.1-windows-x64.zip](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases/download/v0.13.1/BioReader-v0.13.1-windows-x64.zip)，选择“全部解压缩”，再打开 `BioReader` 文件夹中的 `BioReader.exe`。保留同目录的 **`BioReaderService` 文件夹及所有随附文件**；不要从 ZIP 内运行，也不要只复制 exe。服务目录内的 `_internal` 包含 Python 运行时和阅读依赖。
3. Windows 版使用 Microsoft Edge WebView2 Runtime。若电脑缺少它，从[微软官方页面](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)安装 **Evergreen Runtime**；可用 Evergreen Bootstrapper，离线安装选 x64 的 Evergreen Standalone Installer。该运行时是独立组件，安装 Edge 浏览器不等于已满足运行时要求，见[微软分发说明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。
4. 更新便携版时先关闭旧版，完整解压新版并从新文件夹启动；确认可用后可移除旧程序文件夹。`%LOCALAPPDATA%\BioReader` 中的数据保留，不要删除该数据目录。

“便携”指程序文件可整体移动，文献库仍默认保存在用户数据目录。安装版和便携版使用同一默认数据位置。实际 Windows 构建、安装与交互验证范围见 [App 验证记录](tests/README.md)。

### 校验与同步发布

发布页的 `SHA256SUMS.txt` 列出 Mac DMG、Windows 安装 EXE 和 Windows ZIP 的 SHA-256。macOS 可在下载目录运行 `shasum -a 256 -c SHA256SUMS.txt`；如果只下载一个附件，其他项目会提示文件缺失，核对已下载包那一项即可。Windows 可用 PowerShell 计算并与清单中的同名文件比较：

```powershell
Get-FileHash .\BioReader-v0.13.1-windows-x64-setup.exe -Algorithm SHA256
Get-FileHash .\BioReader-v0.13.1-windows-x64.zip -Algorithm SHA256
```

两平台从同一个已检查的源码提交构建，同版三份程序附件和合并校验清单核对完成后一起发布。用户下载对应平台的新包即可更新；本版没有应用内自动更新器，也没有跨电脑同步论文库。每台电脑的模型和 Zotero 设置独立保存。

## 在 Mac 上构建

需要 Go 1.27.3 或更新版本、原生 64 位 Python 3.10 或更新版本，以及首次安装依赖所需的网络连接。在项目根目录运行：

```bash
./app/build-macos.sh
```

如默认 `python3` 版本过旧，可指定已有的 Python：

```bash
BIOREADER_PYTHON=/path/to/python3 ./app/build-macos.sh
```

脚本在 `app/.desktop-venv` 安装构建依赖，读取 `SKILL.md` 中的版本，并通过 [`scripts/build_mygo.py`](../scripts/build_mygo.py) 构建 Python 服务和 MyGo 外壳。Apple 芯片 Mac 的结果位于 `app/mygo/build/darwin-arm64/`，包含 `BioReader.app` 和 DMG；当前 v0.13.1 的内部 DMG 文件名为 `bioreader-0.13.1-arm64.dmg`。构建不会自动上传或发布。

这个构建默认没有 Apple 开发者签名和公证。若要提供正式签名的安装包，需要另外完成相应签名、公证与安装测试。

## 在 Windows 上构建

在 Windows x64 环境准备 Go 1.27.3 或更新版本、64 位 Python 3.10 或更新版本和网络连接。在项目根目录的 PowerShell 中运行：

```powershell
.\app\build-windows.ps1
```

脚本创建 `app/.desktop-venv`、安装依赖、读取版本，再构建 Go 外壳、`BioReaderService` 和 NSIS 安装 EXE。结果位于 `app/mygo/build/windows-amd64/`，便携程序入口为其中的 `BioReader.exe`，服务位于同级 `BioReaderService/`。运行界面还需本机安装 WebView2 Runtime。构建脚本不会自动公开 Release。

仓库另有 [Windows 构建工作流](../.github/workflows/windows-build.yml)，在 Windows runner 上构建并保存工件。对应平台的构建、原生窗口诊断和安装检查须在该平台实际运行；浏览器或 macOS 测试不能代替 Windows 安装验证。

## 打包验收与原生诊断

已安装构建依赖时，也可在目标系统直接指定版本及 Python：

```bash
python scripts/build_mygo.py --version v0.13.1 --python /path/to/python-with-build-dependencies
```

`--skip-backend` 仅供开发时复用已有服务，会将构建标为 dirty，不能用于最终发布；`--skip-installer` 只构建 Windows 便携目录，不满足本次三附件发布要求。

提交源码并从干净的同一提交完成两平台构建后，在各自系统验收产物。Mac 示例：

```bash
app/.desktop-venv/bin/python scripts/package_mygo.py --version v0.13.1 --commit "$(git rev-parse HEAD)"
```

Windows 的安装检查会静默安装并卸载应用，只应在一次性的测试环境或 CI runner 上运行：

```powershell
$sourceCommit = git rev-parse HEAD
.\app\.desktop-venv\Scripts\python.exe scripts/package_mygo.py --version v0.13.1 --commit $sourceCommit --test-installer
```

[`scripts/package_mygo.py`](../scripts/package_mygo.py) 验证实际解压的 Windows ZIP 或挂载的 Mac DMG，比对随包前端与 Git 提交、版本和构建标记，并执行原生诊断。输出位于 `app/dist/mygo-release/`：对应平台的程序附件、`SHA256SUMS.txt`、`build-info.json` 和诊断报告。维护者需合并两平台清单，详见[发布维护](../RELEASING.md#当前桌面正式版-v0131)。

独立调用随包程序的诊断入口为 `BioReader --self-test /absolute/path/report.json --gui-test`（Windows 使用 `BioReader.exe`）。它使用隔离临时文献库，启动实际随包服务，检查会话边界、PDF／笔记持久化和子进程退出；`--gui-test` 还打开使用合成数据的可见系统 WebView 窗口，用 PDF.js 渲染合成 PDF。这是原生自动化诊断范围，系统保存对话框、外部浏览器和剪贴板仍需分别做交互验收。这里只描述流程；实际成功结果以 [App 验证记录](tests/README.md) 为准。

界面资源随程序提供；AI 模型调用、DOI 查询和相关文献检索仍需网络。模型 Key 以明文保存在本机设置文件中。MyGo 外壳通过私有管道启动绑定随机回环端口的服务，初次握手建立会话后跳转到不含 token 的地址；不需要用户配置或开放固定服务端口。

## 对照阅读与问答摘录

顶部「专注原文」展开 PDF，「笔记对照」和「边读边问」分别打开笔记或问答。宽窗口可并排阅读，窄窗口仍按单面板切换；正文与补充 PDF 保留连续滚动、缩放和阅读位置。

快捷问题按当前页或选区填入，修改后手动发送。回答可显示 Markdown 表格、列表与代码，点击来源先校验文件再定位。已完成回答可复制，或连同文件名、页码和原文摘录追加到已有笔记；原内容保留，重复点击不会重复追加，保存失败保留草稿。问答使用有界原文片段，不代替完整精读，也不自动执行工具任务。详见 [原文选字与问答](README.md#原文选字与问答)。

## 笔记原文引用与阅读导航

新生成的精读笔记可带有原文来源按钮，点击后重新核对文件与笔记版本，再跳到相应正文或补充 PDF 页面；缺失、删除或已变化的来源不会被当作可定位原文。已有笔记保留，旧笔记不会被自动补造来源。

原文区的「阅读导航」可展开 PDF 自带章节、当前文件已提取图表和笔记标题。没有原生书签时明确显示空态；图表按正文／补充文件区分，点击可联动原文页和已有笔记中的对应图表位置。

## 本机搜索与资源表

侧栏「搜索正文与笔记」检索本机正文、补充 PDF 文本层和已保存笔记，不调用模型或联网。首次搜索旧 PDF 时生成本地文本缓存可能需要等待；无文本层的扫描件不能搜索。PDF 结果校验来源版本后跳页，有坐标时高亮；笔记结果打开已保存笔记，尚不定位笔记中的具体句子。

「数据与复现」可直接保存 7 个样品字段，按物种、组织、疾病、技术、平台组合筛选，并将当前结果导出为资源 CSV 或去重编号 TXT。论文实际样本量与 GEO 收录总量分开，原来的队列分组和人工修改继续保留。详细步骤见 [App 使用说明](README.md#数据与复现)。本版没有新增备份／恢复界面或自动更新器；数据仍需按上述目录说明自行备份，更新 App 不会自动迁移其他位置的文献库。


## 可选的 Zotero 连接

Zotero MCP 是独立运行的服务，不包含在 BioReader 安装包中。尚未安装时，可先在设置中保存 MCP 地址与本机存储目录；之后按 [Zotero 连接说明](README.md#连接-zotero-mcp) 安装、启动服务并测试连接。默认地址为 `http://127.0.0.1:8000/mcp`，默认目录为 `~/Zotero/storage`。

`localhost` / `127.0.0.1` 指每位使用者自己的电脑，同样的地址不会连接开发者的文库。`localhost:23119/api/` 是 Zotero 本地 API，需要与另行启动的 MCP 服务地址区分。默认附件目录按运行时用户的主目录生成，安装包不预置个人文库配置或凭据。

导入只读取用户选择的条目：元数据可独立导入，PDF 需为该目录内已下载的 Zotero 存储附件。复制到 BioReader 后，原 Zotero 文件和记录保持不变；命中已有来源或 DOI 时返回已有文献，可点击打开，不覆盖笔记。首版只有本机回环地址上的 MCP 可使用本机 PDF 导入；远端 MCP 仅支持元数据导入。Zotero 设置单独写入数据目录中的 `zotero.json`，不改变模型配置。

## 验证与限制

v0.13.1 修复推理模型的读图与长文整理预算，识别截断结果并保留已有笔记；智谱官方指定模型的轻量推理适配只作用于中间整理，最终报告的推理设置保持不变。保留外观、读图查看器、补充图图注修复、中英界面、Go + MyGo 桌面外壳、原文引用、阅读导航、问答摘录、样品资源、全库搜索与 Zotero MCP 导入。验证范围及实际回归、浏览器交互和打包结果见 [App 验证记录](tests/README.md)；合成材料与模拟回答不等于真实模型或精读科学质量评测。问答仍限于所取原文片段，不包含整库 AI 问答或自主工具执行。原生 WebKit、Windows WebView2 与各系统保存对话框应以该验证记录列明的实际结果为准，不能把 Chrome 浏览器测试当作两平台原生窗口验收。

### v0.9.0 包格式（历史）

v0.9.0 使用原 Python 桌面外壳，macOS 和 Windows 均提供 ZIP 预览包；它的附件和验证记录保留在 [v0.9.0 发布页](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases/tag/v0.9.0)。自 v0.10.0 起使用上述 MyGo 构建与 DMG／安装 EXE／便携 ZIP 流程，旧版数据目录保持兼容。

### v0.4.0 历史发布快照

v0.4.0 的桌面包用于预览试用。应用内部使用系统 WebKit 和本机后台，不依赖外部浏览器；不等于纯原生界面或完全离线 AI。

本地已完成 Apple 芯片 Mac 的构建、应用包完整性与 Info.plist 检查。实际编译包在独立临时数据目录中通过启动、本地 PDF.js／字体／docx 资源加载、Cookie／Host／Origin 限制、PDF 上传／下载／删除及笔记持久化检查。图形界面自动检查曾超时，PDF 视觉显示、macOS 保存对话框、Word 导出和真实 AI 生成仍需人工验收，由 [Issue #6](https://github.com/Gaoyuan-0423/biomedical-paper-reader/issues/6) 跟踪。CI 包含 29 项阅读记录、发布元数据和桌面回归，不代表桌面 GUI 验收。

当前开发分支补充了保存／切换／AI 中断回归和桌面窗口验收，具体范围见 [App 验证记录](tests/README.md)。上述 v0.4.0 发布快照不因分支修复而改变；更新构建仍未进行 Apple 开发者签名或公证。

当前源码回归命令（`pywebview` 用于保留的旧桌面兼容测试，不是 MyGo 运行依赖）：

```bash
python -m pip install -r app/backend/requirements.txt 'pywebview>=6.2.1,<7'
python -m unittest discover -s app/tests -v
node --test app/tests/test_*.cjs
go -C app/mygo test ./...
```

macOS 包在 macOS 上构建，Windows 包在 Windows x64 环境中构建；跨系统编译结果不能替代对应平台验证。应用包作为 Release 附件提供，虚拟环境、个人论文库和编译产物不提交到源码仓库。

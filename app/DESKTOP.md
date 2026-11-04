# BioReader macOS / Windows 桌面版

桌面版在独立窗口运行，关闭窗口会停止本地后台服务。PDF、笔记和设置保存在用户数据目录，与程序文件分开：

| 系统 | 默认数据目录 |
|---|---|
| macOS | `~/Library/Application Support/BioReader/` |
| Windows | `%LOCALAPPDATA%\BioReader` |

设置 `BIOREADER_DATA_DIR` 可使用自定义目录。更新前退出 BioReader，再替换程序文件，已有论文、笔记和设置继续保留。源码网页版的 `app/data/` 不会自动迁移；需要迁移时先备份，再复制其中的内容到对应用户数据目录。

## 下载与安装预览包

下载入口：[v0.9.0 桌面预览版](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases/tag/v0.9.0)，附件以发布页实际提供为准。提供 Apple 芯片 Mac（arm64）与 Windows 10 / 11（x64）预览包；没有 Intel Mac、Windows ARM64 原生包或 Linux 安装包。桌面包不需要另装 Python 或手动启动浏览器／终端。

### macOS

1. 下载 [BioReader-v0.9.0-macos-arm64.zip](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases/download/v0.9.0/BioReader-v0.9.0-macos-arm64.zip)。
2. 解压 ZIP，将 `BioReader.app` 拖入“应用程序”，或从解压目录直接打开。更新旧版前先退出正在运行的 BioReader。
3. 双击打开 App。AI 笔记需要在设置中配置模型接口和 API Key。

macOS 包未进行 Apple 开发者签名和公证。macOS 可能阻止首次打开；包完整性检查不代表 Apple 公证或所有 Mac 的安装验收。

### Windows

1. 下载 [BioReader-v0.9.0-windows-x64.zip](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases/download/v0.9.0/BioReader-v0.9.0-windows-x64.zip)，选择“全部解压缩”。将解压后的完整文件夹保存在你选择的位置。
2. Windows 版使用 Microsoft Edge WebView2 Runtime。若电脑缺少它，从[微软官方页面](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)安装 **Evergreen Runtime**；可用 Evergreen Bootstrapper，离线安装选 x64 的 Evergreen Standalone Installer。该运行时是独立组件，安装 Edge 浏览器不等于已满足运行时要求，见[微软分发说明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。
3. 打开文件夹中的 `BioReader.exe`。保留 `_internal` 等全部随附文件，不要从 ZIP 内运行，也不要只复制 exe。需要桌面入口时为 exe 创建快捷方式。
4. 更新时先关闭旧版，完整解压新版并从新文件夹启动；确认可用后可移除旧程序文件夹。`%LOCALAPPDATA%\BioReader` 中的数据保留，不要删除该数据目录。

Windows ZIP 是便携程序包，不是安装向导；“便携”指程序文件可整体移动，文献库仍默认保存在用户数据目录。实际 Windows 构建与交互验证范围见 [App 验证记录](tests/README.md)。

### 校验与同步发布

发布页的 `SHA256SUMS.txt` 列出各平台 ZIP 的 SHA-256。macOS 可在下载目录运行 `shasum -a 256 -c SHA256SUMS.txt`；若只下载了一个平台包，另一项会提示文件缺失，核对已下载包那一项即可。Windows 可用 PowerShell 计算并与清单中的同名文件比较：

```powershell
Get-FileHash .\BioReader-v0.9.0-windows-x64.zip -Algorithm SHA256
```

macOS 和 Windows 以同一个版本、同一个检查过的源码提交发布，更新功能保持一致。用户下载对应平台的新包即可更新；本版没有应用内自动更新器，也没有跨电脑同步论文库。每台电脑的模型和 Zotero 设置独立保存。

## 在 Mac 上构建

需要 Python 3.10 或更新版本以及网络连接，以便首次安装构建依赖。在项目根目录运行：

```bash
./app/build-macos.sh
```

如默认 `python3` 版本过旧，可指定已有的 Python：

```bash
BIOREADER_PYTHON=/path/to/python3 ./app/build-macos.sh
```

构建完成后双击 `app/dist/BioReader.app`。该脚本创建独立虚拟环境，打包当前机器的架构，构建结果不会自动上传或发布。

这个构建默认没有 Apple 开发者签名和公证。若要提供正式签名的安装包，需要另外完成相应签名、公证与安装测试。

界面依赖已随程序提供；AI 模型调用、DOI 查询和相关文献检索仍需网络。AI 笔记需要在设置中配置可用的模型接口及 API Key；Key 以明文保存在本机设置文件中。导出文件时使用现有的下载／保存流程。

## 在 Windows 上构建

在 Windows x64 环境安装 64 位 Python 3.10 或更新版本，并准备网络连接以安装构建依赖。在项目根目录的 PowerShell 中运行：

```powershell
.\app\build-windows.ps1
```

脚本使用 `app/BioReader-windows.spec` 打包独立目录，完成后从 `app/dist/BioReader/BioReader.exe` 启动。分发时保留整个 `BioReader` 文件夹，包括 `_internal`。运行界面还需本机安装 WebView2 Runtime；在 macOS 上执行 Python 测试不能代替 Windows 构建。构建脚本不会自动公开 Release。

仓库另有 [Windows 构建工作流](../.github/workflows/windows-build.yml)，在 Windows runner 上构建并保存下载工件。维护者发布时需核对运行的源码提交，与 macOS 包保持一致，再将同版两个平台包和合并的校验清单上传到 Release。实际构建、自检和 GUI 验收结果分别记录，不将未执行的步骤称为通过。

## 对照阅读与问答摘录

顶部「专注原文」展开 PDF，「笔记对照」和「边读边问」分别打开笔记或问答。宽窗口可并排阅读，窄窗口仍按单面板切换；正文与补充 PDF 保留连续滚动、缩放和阅读位置。

快捷问题按当前页或选区填入，修改后手动发送。回答可显示 Markdown 表格、列表与代码，点击来源先校验文件再定位。已完成回答可复制，或连同文件名、页码和原文摘录追加到已有笔记；原内容保留，重复点击不会重复追加，保存失败保留草稿。问答使用有界原文片段，不代替完整精读，也不自动执行工具任务。详见 [原文选字与问答](README.md#原文选字与问答)。

## 本机搜索与资源表

侧栏「搜索正文与笔记」检索本机正文、补充 PDF 文本层和已保存笔记，不调用模型或联网。首次搜索旧 PDF 时生成本地文本缓存可能需要等待；无文本层的扫描件不能搜索。PDF 结果校验来源版本后跳页，有坐标时高亮；笔记结果打开已保存笔记，尚不定位笔记中的具体句子。

「数据与复现」可直接保存 7 个样品字段，按物种、组织、疾病、技术、平台组合筛选，并将当前结果导出为资源 CSV 或去重编号 TXT。论文实际样本量与 GEO 收录总量分开，原来的队列分组和人工修改继续保留。详细步骤见 [App 使用说明](README.md#数据与复现)。本版没有新增备份／恢复界面或自动更新器；数据仍需按上述目录说明自行备份，更新 App 不会自动迁移其他位置的文献库。


## 可选的 Zotero 连接

Zotero MCP 是独立运行的服务，不包含在 BioReader 安装包中。尚未安装时，可先在设置中保存 MCP 地址与本机存储目录；之后按 [Zotero 连接说明](README.md#连接-zotero-mcp) 安装、启动服务并测试连接。默认地址为 `http://127.0.0.1:8000/mcp`，默认目录为 `~/Zotero/storage`。

`localhost` / `127.0.0.1` 指每位使用者自己的电脑，同样的地址不会连接开发者的文库。`localhost:23119/api/` 是 Zotero 本地 API，需要与另行启动的 MCP 服务地址区分。默认附件目录按运行时用户的主目录生成，安装包不预置个人文库配置或凭据。

导入只读取用户选择的条目：元数据可独立导入，PDF 需为该目录内已下载的 Zotero 存储附件。复制到 BioReader 后，原 Zotero 文件和记录保持不变；命中已有来源或 DOI 时返回已有文献，可点击打开，不覆盖笔记。首版只有本机回环地址上的 MCP 可使用本机 PDF 导入；远端 MCP 仅支持元数据导入。Zotero 设置单独写入数据目录中的 `zotero.json`，不改变模型配置。

## 验证与限制

v0.9.0 改进阅读布局、问答排版、上下文快捷问题和回答摘录，保留样品资源、全库搜索与 Zotero MCP 导入。验证范围及实际回归、浏览器交互和打包结果见 [App 验证记录](tests/README.md)；合成材料与模拟回答不等于真实模型或精读科学质量评测。问答仍限于所取原文片段，不包含整库 AI 问答或自主工具执行。原生 WebKit、Windows WebView2 与各系统保存对话框应以该验证记录列明的实际结果为准，不能把 Chrome 浏览器测试当作两平台原生窗口验收。

### v0.4.0 历史发布快照

v0.4.0 的桌面包用于预览试用。应用内部使用系统 WebKit 和本机后台，不依赖外部浏览器；不等于纯原生界面或完全离线 AI。

本地已完成 Apple 芯片 Mac 的构建、应用包完整性与 Info.plist 检查。实际编译包在独立临时数据目录中通过启动、本地 PDF.js／字体／docx 资源加载、Cookie／Host／Origin 限制、PDF 上传／下载／删除及笔记持久化检查。图形界面自动检查曾超时，PDF 视觉显示、macOS 保存对话框、Word 导出和真实 AI 生成仍需人工验收，由 [Issue #6](https://github.com/Gaoyuan-0423/biomedical-paper-reader/issues/6) 跟踪。CI 包含 29 项阅读记录、发布元数据和桌面回归，不代表桌面 GUI 验收。

当前开发分支补充了保存／切换／AI 中断回归和桌面窗口验收，具体范围见 [App 验证记录](tests/README.md)。上述 v0.4.0 发布快照不因分支修复而改变；更新构建仍未进行 Apple 开发者签名或公证。

运行测试：

```bash
python -m pip install -r app/backend/requirements.txt 'pywebview>=6.2.1,<7'
python -m unittest discover -s app/tests -v
node --test app/tests/test_frontend.cjs app/tests/test_evidence.cjs app/tests/test_resources.cjs app/tests/test_chat.cjs app/tests/test_zotero.cjs app/tests/test_library_search.cjs
```

macOS 包在 macOS 上构建，Windows 包在 Windows x64 环境中构建；跨系统编译结果不能替代对应平台验证。应用包作为 Release 附件提供，虚拟环境、个人论文库和编译产物不提交到源码仓库。

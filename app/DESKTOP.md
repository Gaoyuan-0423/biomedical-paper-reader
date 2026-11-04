# BioReader macOS 桌面版

桌面版在独立窗口运行，关闭窗口会停止本地后台服务。PDF、笔记和设置默认保存在 `~/Library/Application Support/BioReader/`，不会随替换 App 一同删除。现有网页版的 `app/data/` 不会自动迁移；请先备份后再手动复制其中的内容到这个目录。

## 下载与安装预览包

下载入口：[v0.8.0 桌面预览版](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases/tag/v0.8.0)，附件以发布页实际提供为准。本次预览包适用于 Apple 芯片 Mac（arm64），不包含 Intel Mac、Windows 或 Linux 安装包。

1. 下载 [BioReader-v0.8.0-macos-arm64.zip](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases/download/v0.8.0/BioReader-v0.8.0-macos-arm64.zip)；需要校验时，同时下载发布页的 `SHA256SUMS.txt`。
2. 解压 ZIP，将 `BioReader.app` 拖入“应用程序”，或从解压目录直接打开。更新旧版前先退出正在运行的 BioReader。
3. 双击打开 App。无需另装 Python 或手动启动浏览器／终端。AI 笔记需要在设置中配置模型接口和 API Key。

这是 GitHub Pre-release 预览包，未进行 Apple 开发者签名和公证。macOS 可能阻止首次打开；包完整性检查不代表 Apple 公证或所有 Mac 的安装验收。完整验证范围见下文。

如已下载 ZIP 和校验文件，可在下载目录运行 `shasum -a 256 -c SHA256SUMS.txt` 核对文件是否完整。

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

## 本机搜索与资源表

侧栏「搜索正文与笔记」检索本机正文、补充 PDF 文本层和已保存笔记，不调用模型或联网。首次搜索旧 PDF 时生成本地文本缓存可能需要等待；无文本层的扫描件不能搜索。PDF 结果校验来源版本后跳页，有坐标时高亮；笔记结果打开已保存笔记，尚不定位笔记中的具体句子。

「数据与复现」可直接保存 7 个样品字段，按物种、组织、疾病、技术、平台组合筛选，并将当前结果导出为资源 CSV 或去重编号 TXT。论文实际样本量与 GEO 收录总量分开，原来的队列分组和人工修改继续保留。详细步骤见 [App 使用说明](README.md#数据与复现)。本版没有新增备份／恢复界面或自动更新器；数据仍需按上述目录说明自行备份，更新 App 不会自动迁移其他位置的文献库。


## 可选的 Zotero 连接

Zotero MCP 是独立运行的服务，不包含在 BioReader 安装包中。尚未安装时，可先在设置中保存 MCP 地址与本机存储目录；之后按 [Zotero 连接说明](README.md#连接-zotero-mcp) 安装、启动服务并测试连接。默认地址为 `http://127.0.0.1:8000/mcp`，默认目录为 `~/Zotero/storage`。

`localhost` / `127.0.0.1` 指每位使用者自己的电脑，同样的地址不会连接开发者的文库。`localhost:23119/api/` 是 Zotero 本地 API，需要与另行启动的 MCP 服务地址区分。默认附件目录按运行时用户的主目录生成，安装包不预置个人文库配置或凭据。

导入只读取用户选择的条目：元数据可独立导入，PDF 需为该目录内已下载的 Zotero 存储附件。复制到 BioReader 后，原 Zotero 文件和记录保持不变；命中已有来源或 DOI 时返回已有文献，可点击打开，不覆盖笔记。首版只有本机回环地址上的 MCP 可使用本机 PDF 导入；远端 MCP 仅支持元数据导入。Zotero 设置单独写入数据目录中的 `zotero.json`，不改变模型配置。

## 验证与限制

v0.8.0 增加结构化样品整理、组合筛选、CSV／编号导出与全库本地搜索，保留 Zotero MCP 逐篇导入和原文问答。本轮验证使用隔离合成材料，不读取私人论文库或调用真实模型，未重新评测精读内容质量。搜索不包含 OCR、语义检索或跨论文 AI 问答；原生 WebKit 本轮未重新验收。具体自动回归、浏览器交互和打包结果见 [App 验证记录](tests/README.md)。

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

打包结果由构建机器的架构决定；本次发布预览包为 arm64。应用包作为 Release 附件提供，虚拟环境、个人论文库和编译产物不提交到源码仓库。

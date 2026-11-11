# CLAUDE.md

本文件为 AI 协作者（及新贡献者）提供本仓库的工作约定。面向用户的文档在 README.md，不要在这里重复。

biomedical-paper-reader 是本地单用户的生物医学论文精读工具：`SKILL.md` + `references/` 是给 AI 代理用的精读方法论，`app/` 是按同一方法论实现的本地阅读应用（FastAPI + 原生 JS，桌面版用 Go + MyGo 承载同一界面）。两条线相互独立：改 skill 不必动 app，反之亦然。界面文案全部中文，代码注释全部英文。

## 命令

Python 一律用项目自带的 `app/.desktop-venv`（uv 管理的 CPython 3.12.11）；CI 用 3.11，避免只用 3.12+ 语法。

```sh
app/.desktop-venv/bin/python app/tests/test_tags.py            # 单个后端测试文件
app/.desktop-venv/bin/python -m unittest discover -s app/tests # 后端全量（约 312 项，约 16s）
node --test app/tests/test_frontend.cjs                        # 前端回归，其余 *.cjs 同理逐个跑
node --check app/frontend/app.js && node --check app/frontend/export.js
app/.desktop-venv/bin/python evals/validate_repo.py            # 链接与历史验收文件完整性（CI 也跑）
app/.desktop-venv/bin/python evals/test_reading_record.py
app/.desktop-venv/bin/python evals/test_prepare_release.py
git diff --check
./app/run.sh                                                   # 源码启动，默认 127.0.0.1:8765，PORT 可改
```

- 没有配置格式化工具，代码是手工排版的长行风格（超 100 列常见，单行 `if x: return y` 也有）；不要跑任何 formatter，跟随周围风格。
- 新增后端测试沿用现有模式：unittest + `tempfile.TemporaryDirectory` + `patch.object(main, 'DATA_DIR'/'PAPERS_DIR'/'TRASH_DIR'/'SETTINGS_FILE', ...)` + TestClient；PDF 一律用 `test_documents.fixture_pdf()` 等原创合成材料；网络一律 AsyncMock / `httpx.MockTransport`，测试绝不联网、绝不读用户真实数据。
- 后端测试文件之间互相导入（`from test_documents import fixture_pdf, main, TestClient`），依赖脚本目录在 sys.path，单独执行文件即可。

## 架构

### app/backend（FastAPI，模块按职责拆分）

- `main.py`：路由与存储核心。所有论文数据只有一个写入口 `write_meta`（atomic_json + 过滤 DERIVED_FIELDS）；`read_meta` / `paper_dir` / `list_meta` 是读取咽喉。`storage_lock`（RLock）保护存储事务，端点内窄范围持锁，锁内不得调用会再取锁的阻塞外部操作。
- `reading_workflow.py`：逐图/证据/材料修订的存储语义（`defaults`、`atomic_json`、`PAPER_SCHEMA_VERSION`、`storage_lock`）。
- 其余模块各管一域：`figures`/`caption_migration`（取图与图注）、`paper_chat`/`chat_sources`/`note_sources`（问答与来源快照）、`llm`（请求构造、流式解析、错误分类；`_request_stream` 是唯一 HTTP 边界）、`paper_resources`/`resource_catalog`/`resource_extraction`/`geo_metadata`/`research_export`（数据与复现）、`zotero_import`/`zotero_mcp`（Zotero MCP 导入）、`library_search`（本地检索）、`note_assets`、`reading_state`、`paper_tags`、`ui_preferences`、`external`（Crossref / Semantic Scholar）。

### 数据布局

- 数据根目录：源码安装为 `app/data/`，桌面版 macOS `~/Library/Application Support/BioReader/`、Windows `%LOCALAPPDATA%\BioReader`；`BIOREADER_DATA_DIR` 可整体重定向（**在 import 时读取**，测试要 patch 模块常量而不是改环境变量）。
- 每篇论文一个文件夹 `papers/<uuid-hex>/`：`meta.json`、`paper.pdf`、`text.txt`、`figures/`、`note-sources/`、`supplements/` 等。附件相对路径，文件夹可整体拷贝。
- `trash/<uuid-hex>/`：删除论文先进回收站（`DELETE /api/papers/{id}` → `GET /api/trash` → `POST /api/trash/{id}/restore` / `DELETE /api/trash/{id}` 彻底删除）。DOI 与 Zotero 去重把回收站也算入，回收站中的条目阻止重复导入。
- `meta.json` 带 `schema_version`（`reading_workflow.PAPER_SCHEMA_VERSION`）。规则：由更新版本写入的数据一律拒绝而不是误读——`read_meta` / `write_meta` / `paper_dir` 三处守卫返回 409，列表与检索把它标记跳过而不解释。**bump 版本的时机是"旧版构建会误读新数据"时**；`meta.get` 类读取要容忍非 dict 的损坏 JSON。

### app/frontend（原生 JS，零构建）

`app.js` 是主体；`safe-markdown.js` 是安全边界：不执行原始 HTML、不加载 Markdown 内嵌图片、不激活 `figure:` / `note-source:` 内部伪引用，外链仅限安全 HTTP/HTTPS——任何改动不得放宽。i18n 拆成五个文件（`i18n*.js`），切换语言即时生效但绝不翻译论文内容、笔记与模型回答。`vendor/` 内 pdf.js、marked、highlight.js、docx 原样使用，带各自 LICENSE 文件。

### 桌面壳与打包

`app/mygo`（Go + MyGo）承载同一前端，PyInstaller 打包 Python 服务（`BioReader-service.spec` / `BioReader-windows.spec`）；构建脚本 `app/build-macos.sh` / `app/build-windows.ps1`。macOS 包未签名未公证。发布流程见 `RELEASING.md`，没有应用内自动更新。

## 不变量（改动前先确认不会破坏）

- **用户数据不可再生，一律保守处理**：删除进回收站而不是直接 rmtree；生成失败/中断保留已有笔记与快照；重复导入命中时保留原记录与笔记；人工编辑过的内容（标签、图注）不被自动流程覆盖。
- **报告与证据分离**：正文笔记讲科学含义；页码、查看事件、证据索引存 sidecar（`references/evidence-rules.md`）。引用只允许来自本次真实提供的原文片段，旧笔记不自动补出处。
- **补充材料默认只用本地已有文件**，用户明确要求才联网。
- **措辞强度与证据相称**：观察不写成因果、统计单位逐项区分（`references/study-checks.md`）。
- 接口响应向后兼容：给列表项加字段可以，改既有字段含义/结构必须同步前端与 `.cjs` 测试。

## 已知坑

- `main.py` 在 import 时读 `BIOREADER_DATA_DIR` 并计算所有目录常量；测试里 patch 模块属性，运行隔离实例时在进程启动前设环境变量。
- `schema_version_of` 等对 `meta.json` 的早期读取必须容忍损坏文件（非 dict、坏 JSON、OSError）——损坏文件的既有语义是"各扫描点自行跳过并出 warning"，不要让整库 500。
- 同一秒内创建的多篇论文 `added_at` 相同，列表顺序不稳定：测试断言用集合，不要断言顺序。
- Zotero 附件只读本机受管文件，远程 MCP 端点明确拒绝读附件。
- 沙箱/受限终端里系统 `python3` 可能缺依赖，miniforge python 可能无法执行；用 `app/.desktop-venv/bin/python`。

## 文档地图

- `SKILL.md` + `references/`：精读方法论（报告模板、模式、证据规则、研究检查点、材料处理、逐图记录）。
- `app/README.md`：应用功能与行为细节；`app/DESKTOP.md`：安装/构建/数据位置。
- `app/tests/README.md`：按时间追加的验证记录，新版本在小节末尾追加，不重写历史。
- `evals/PROTOCOL.md` / `cases.md` / `RESULTS.md` / `runs/`：行为验收；`examples/` 为三份完整示例报告。`validate_repo.py` 校验它们之间的链接与文件完整性——移动或重命名这些文件前先看它检查什么。
- `CHANGELOG.md`：每个版本一条；发布版本与 Releases 页面对应，正式 Release 可能晚于 main。

# 桌面阅读稳定性验证 — 2026-11-03

本次为 v0.4.0 之后的开发分支修复，未创建新 Release。测试仅使用隔离目录中的原创合成 PDF 和笔记；未读取用户 API Key 或私人论文库。

## 自动回归

- `node --test app/tests/test_frontend.cjs`：17 项。直接加载交付的 `app.js`，用可控延迟模拟保存乱序、保存失败／重试、切换等待、快速选择、加载期间继续编辑、旧图库响应、AI 中断／停止／完成确认、PDF 首页面加载过期／渲染取消，以及 Word 表格与导出对象隔离。DOM 与网络为测试替身，不替代真实窗口验收。
- `python -m unittest discover -s app/tests -v`：4 项，通过会话、Host／Origin、多实例 Cookie 隔离、源码／打包资源路径及 PDF／笔记持久化接口验证。
- `python3 evals/test_reading_record.py`：15 项；`python3 evals/test_prepare_release.py`：10 项。
- `python3 evals/validate_repo.py --self-test`：4 项，包含构建目录排除而仍检查 vendor 源码文档的回归；`python3 evals/validate_repo.py`：链接与历史验收文件完整性通过。
- `node --check app/frontend/app.js`、`node --check app/frontend/export.js` 与 `git diff --check` 通过。

前端回归已加入 `.github/workflows/ci.yml`。开发检查使用 Node.js 24.14.1、Python 3.12.11；源码和既有回归继续兼容 CI 的 Python 3.11。

## macOS 窗口验收

使用本机 arm64 构建、pywebview 6.2.1、PyInstaller 6.22.3 和应用随附的 WebKit 页面，在独立数据目录中检查：

- 三页合成 PDF 的实际显示、放大、适宽和翻页。
- 在编辑框输入中文 Markdown 后立即切换到另一篇论文，再返回确认内容保留；退出再打开后检查持久化。
- Word 导出调起 macOS 原生保存对话框，保存后检查 DOCX ZIP、正文 XML、中文标题／段落和真实表格结构。
- 构建完成与 `codesign --verify --deep --strict` 应用包完整性检查。此检查不是 Apple 开发者签名或公证。

本地测试产物放在被忽略的 `.validation/desktop-stability/`；不将测试库、构建环境或 App 包提交到源码仓库。

## 未覆盖范围

未使用真实付费模型接口，AI 流的成功、停止和异常由模拟事件验证；未重跑论文科学解读质量评测。未验证 Intel Mac、其他 macOS 版本、公众号剪贴板、系统强制退出或崩溃恢复。失败保存的草稿当前保留在运行中的编辑器；请在退出前重试保存或导出。Word 表格保留单元格文本和表头，单元格中的复杂富文本仍不保证完整还原。

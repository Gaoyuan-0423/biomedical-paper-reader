# v0.7.0 Zotero MCP 导入工程验证

日期：2026-11-04。范围：面向 54yyyu/zotero-mcp 的 Streamable HTTP 连接、JSON／SSE 响应、检索预览、JSON 元数据、附件筛选、本机存储 PDF 导入与查重。

`SKILL.md` 仅更新版本，六份 references 与 v0.6.0 哈希一致；论文阅读行为及笔记生成规则未改变。当前哈希见 [manifest](manifest.json)。`cases: []` 表示没有新增真实论文试读或科学质量评分。

本轮协议资料来自[上游工具源码](https://github.com/54yyyu/zotero-mcp/tree/main/src/zotero_mcp/tools)、[官方测试样例](https://github.com/54yyyu/zotero-mcp/blob/main/tests/test_metadata_formats.py)和 [Zotero 本地 API 文档](https://www.zotero.org/support/dev/web_api/v3/local_api)。验收使用隔离合成 PDF 与模拟 MCP 响应，不连接私人 Zotero 库、不读取真实凭据、不安装用户服务或调用真实模型。没有将模拟服务器响应表述为真实 Zotero 库端到端实测。

App 回归、浏览器交互和打包结果统一记录于 [App 验证记录](../../../app/tests/README.md)。检查关注连接及错误恢复、导入方式明确选择、附件与父条目关系、配置目录内文件读取、重复记录保护及已有阅读／问答行为。原生 WebKit 本轮未重新验收；应用仍无 Apple 开发者签名或公证。

# v0.5.0 研究工作台工程验证记录

日期：2026-11-03。范围：蓝白界面、阅读状态与待办、关键证据、数据与代码资源、导出附录、损坏记录隔离，以及发布版本资料。

论文阅读行为要求未改动。本轮逐字比较 `SKILL.md` 与版本更新前的内容，唯一差异是版本标识从 v0.4.0 改为 v0.5.0；六份 `references/` 指令文件的 SHA-256 与 v0.4.0 manifest 一致。当前指令哈希记录于本目录的 [manifest](manifest.json)。

## 实际执行的检查

- `python3 evals/test_reading_record.py -v`：15 项阅读记录回归通过。
- `python3 evals/test_prepare_release.py -v`：10 项发布元数据回归通过。
- `python3 evals/validate_repo.py --self-test`：4 项完整性校验器自测通过。
- `python3 scripts/prepare_release.py v0.5.0 --output /private/tmp/biomedical-v0.5.0-validated-notes.md`：README 中英文版本、SKILL、CHANGELOG 与发布说明一致性检查通过；此命令只生成经过检查的发布说明，没有发布任何内容。
- `python3 evals/validate_repo.py`：本地链接、锚点和本轮指令哈希检查通过，默认选择本目录 manifest。
- 本轮主任务执行了 46 项 App Python 回归与 69 项 JavaScript 回归，全部通过。范围包含状态与笔记并发保存、证据／资源来源核查、跨论文资源关联、导出附录、损坏侧边记录隔离，以及前端草稿保护和延迟响应处理。具体交互验证与未覆盖范围见 [App 验证记录](../../../app/tests/README.md)。
- `git diff --check`：通过。

## 未执行与结果边界

未重新运行真实论文、视觉材料或语言／阅读模式行为试读，没有新的论文解读输出、语义评分、准确率或性能结论。manifest 的 `cases` 为空，明确表示本轮没有论文试读产物，不表示这些行为已经通过新版测试。

应用回归使用隔离测试资料与原创合成 PDF；模型调用由测试替身验证，没有调用真实付费模型或读取私人论文库。应用包完整性、浏览器交互和源码回归的范围不同，不能据此声称通过所有平台、原生窗口或外部公众号编辑器验收。本记录不声明 GitHub 托管 CI、Release 上传、Apple 开发者签名或公证已经完成。

历史 v0.4.0 及更早的 manifest、原始输出和验收记录均保留原样。完整性校验继续核对当前版本的完整指令哈希，没有跳过版本差异或放宽校验规则。

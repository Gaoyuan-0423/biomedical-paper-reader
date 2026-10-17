# v0.3.3 工程与结构修订记录

日期：2026-10-17。本版不新增行为要求，未重跑行为试读。

## 修订内容

- 入口 `SKILL.md` 重写为一条一规则短句结构并加版本标注，参考文件改为加载表；全部行为要求保留在入口或由归属 reference 文件承载。
- 规则去重：四组跨文件重复规则收敛为“归属文件完整陈述 + 其他位置链接”（转换／复制不算查看、速读范围边界、报告与过程记录分离、补充材料本地默认）。
- 材料处理新增长论文与上下文管理流程（逐图笔记落盘、报告组装、断点续做）。
- 新增 CI（`.github/workflows/ci.yml`：[仓库完整性检查](../../validate_repo.py)、[记录回归](../../test_reading_record.py)、脚本编译）、可选脚本 `scripts/render_pdf_page.py`、[自备论文评测协议](../../PROTOCOL.md)。
- README 重构并新增英文简介；examples 中 observational 与 prediction-method 重排为现行报告格式（沿用 v0.1.0 已验收分析，维护侧生成、非盲测评），incomplete 复核后保留。

## 验证范围

- `python3 evals/test_reading_record.py` 全部通过；`python3 evals/validate_repo.py` 以本目录 [manifest](manifest.json) 为最新运行记录校验通过。
- `scripts/render_pdf_page.py` 在本机 PyMuPDF 与 pypdfium2 两个后端上实测渲染、裁剪与页码越界报错；无后端时的退出提示路径未实测（本机两后端均可用），仅经代码审阅。
- 未重新试读真实论文、视觉材料、语言／模式案例或 v0.3.2 两例；历史结论保留原版本归属，不能算本版重跑。examples 重排不作为行为验收依据。
- 指令文件哈希记录于 [manifest](manifest.json)；本目录不含试读产物，`cases` 为空，本记录不构成任何行为通过声明。

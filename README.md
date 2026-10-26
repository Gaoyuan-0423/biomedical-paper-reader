# biomedical-paper-reader

**单篇生物医学论文精读：看懂实验为什么做，判断证据支持到哪里，提炼可以借鉴的研究设计。**

版本：v0.3.3。默认中文完整精读，支持英文及明确要求的中英双语；另有速读和指定图／方法解析。

A Chinese-first agent skill for close reading, quick screening, or focused figure/method analysis of one biomedical paper, with traceable evidence records and the same evidence standards in any language.

[Skill 入口](SKILL.md) · [示例报告](examples/README.md) · [验收记录](evals/RESULTS.md) · [MIT 许可](LICENSE)

## 报告长什么样

> **一句话发现：** 在 18 名供者的横断面组织比较中，病例的 Marker A 平均分数较高，但供者层面差值的置信区间包含 0；把细胞当作独立观察得到的强显著性不能证明 A 驱动疾病。
> **阅读建议：选择性阅读。** 适合精看其统计单位与结论之间的关系……
> **本文看点：** 同一数据使用不同统计单位得到不同显著性；组间年龄差异提供了需要检验的替代解释；观察结果与 Discussion 的因果措辞不匹配。

——摘自[观察研究示例](examples/observational/report.md)。三份完整示例（二手片段、观察研究、预测方法）见[示例目录](examples/README.md)。

## 输入与模式

提供 PDF、DOI、论文链接或原文即可开始，无需先写研究问题。二手解析或部分材料也能处理，但会明确覆盖范围与暂定判断；仅有短片段时合并无实质内容的章节，篇幅来自原文信息量。

| 模式 | 实际读取与交付 |
|---|---|
| 完整精读（默认） | 通读可用正文和方法，逐张概览已有主图／补图，必要面板深入核查；交付论证与逐图／结果解释，未完成范围标受限 |
| 速读 | 先读摘要、问题、结果结构与讨论，再定点核查筛选判断依据的图、对照及方法；列明未读范围，不声称全文核验 |
| 指定图／方法 | 解析目标并补足影响解释的上下文、必要对照和已有相关补图；交付局部设计、结果、边界及适配建议 |

完整精读的报告结构：开篇导读 → 文章信息 → 研究概述 → 全文逻辑路线图 → 逐图／逐结果精读 → 研究总结与课题借鉴 → 数据与方法资源。有文件工具时交付独立论文目录下的 `report.md`（图像放 `assets/`，相对路径）；没有文件工具时直接输出文本。详见[报告模板](references/report-template.md)与[模式与语言适配](references/reading-modes.md)。

## 核心规则

- **阅读报告与过程记录分开。** 正文围绕作者的问题、设计、结果和论证；页码清单、查看事件、覆盖表、证据索引独立保存，用户要求核查或审稿时再展示。见[证据规则](references/evidence-rules.md)。
- **补充材料默认只用本地已有文件**，用户明确要求补取才联网下载；附件缺失不当作作者未做验证。
- **实际查看图像后才描述视觉信息。** 转换、复制或插入报告不算查看；逐图状态由[逐图记录](references/reading-record.md)和[标准库校验器](scripts/check_reading_record.py)约束。
- **措辞与推断强度相称。** 观察不写成因果、救援不等于必要性、联合不自动成协同、模型效果不自动成为患者疗效；统计单位与样本层级逐项区分。
- **范围内每个结果单元都要解释**研究问题、方法输入输出与论证衔接，不能只给前几图方法、后续退化为图片或疑点清单。
- **语言由用户要求决定**，英文论文不自动触发英文输出；双语两种表述共用证据记录，数字与判断强度一致。

## 使用

### 直接使用

```bash
git clone https://github.com/Gaoyuan-0423/biomedical-paper-reader.git
```

```text
请使用 ./biomedical-paper-reader/SKILL.md 阅读这份 PDF，生成中文精读报告。
```

路径相对于你的工作目录；必要时改用 `SKILL.md` 的绝对路径。要使用客户端的技能发现功能，将整个目录放入它支持的 skills 目录，保留 `references/`。

### Codex 本地安装与更新

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/Gaoyuan-0423/biomedical-paper-reader.git ~/.codex/skills/biomedical-paper-reader
```

已安装副本用 `git pull` 更新；`SKILL.md` 标题下标注了当前版本，可与 [CHANGELOG](CHANGELOG.md) 对照确认没有装错版本。使用自定义配置目录时改用其对应 skills 目录；已存在同名版本时先比较内容，保留自己的修改。

```text
使用 $biomedical-paper-reader 解析这篇论文：<DOI、链接或附件>。
```

语言与模式直接表达即可，例如“用英文速读这篇论文，判断是否值得精读”或 "analyze Figure 3 in English, including the methods and controls needed to interpret it"。双语或专题方法同样直接说明；这些是分析报告，不是全文逐句翻译。可补充研究方向以获得更贴合的迁移建议；未补充时给条件式适用场景。

## English quick start

Clone the repo and point any file-capable assistant at `SKILL.md`, or drop the folder into your client's skills directory. Default output is Chinese; request English or bilingual reports explicitly. Three modes: full close reading (default), quick screening, and focused figure/method analysis. Inputs can be a PDF, DOI, link, or pasted text. The skill relies only on host-provided file/PDF/image tools — no fixed MCP servers, third-party skills, or paid services. See [examples](examples/README.md) for sample reports (fictional materials) and [RESULTS.md](evals/RESULTS.md) for evaluation records.

## 工作方式

按所选模式建立并复用原文章节、图表和主张索引，再联读相关正文、Methods、图注及图像。核心结论、引用数字和主要批评都有独立可追溯记录；正文以科学含义解释结果。多队列／嵌套样本记录个体→组织→切片→细胞层级及分析单位；机制链逐边说明证据与缺口；方法检查点按研究设计选用，不要求每篇出现固定技术链条。无图论文按主表、结果小节或方法单元展开。未实际执行的代码和数据不会被标为已经复现。长论文按[增量笔记流程](references/material-handling.md)逐图落盘、组装成稿。

## 文件组织

- `SKILL.md`：触发说明、工作流和交付要求。
- `references/`：[报告模板](references/report-template.md)、[模式与语言](references/reading-modes.md)、[证据规则](references/evidence-rules.md)、[研究检查点](references/study-checks.md)、[材料处理](references/material-handling.md)、[逐图记录](references/reading-record.md)。
- `scripts/check_reading_record.py`：逐图记录与声明一致性的标准库校验器。
- `scripts/render_pdf_page.py`：可选 PDF 页渲染辅助（需 pypdfium2 或 PyMuPDF，非运行依赖）。
- `app/`：[BioReader App](app/README.md)——按本 skill 方法论实现的本地文献阅读应用（FastAPI + 原生 JS；PDF 双栏解析与图注锚定取图、精读/速读流式笔记、读图模型、DOI 导入与相关文献、Markdown/Word/公众号一键复制导出），与 skill 本体相互独立，`./app/run.sh` 即用。
- `agents/openai.yaml`：可选的 Codex 界面元数据。
- `evals/cases.md`、`evals/fixtures/`：行为验收设计和原创虚构材料。
- `evals/PROTOCOL.md`：用自备论文复现行为评测的协议。
- `evals/RESULTS.md`：版本对应的验收入口；历史记录与新回归分开。
- `evals/test_reading_record.py`、`evals/validate_repo.py`：执行回归与仓库完整性检查（CI 自动运行）。
- `examples/`、`evals/runs/`：示例与各版本实际试读输出、评分及失败记录。
- `CHANGELOG.md`：版本变化。

## 示范与验证

两篇真实试读材料用于检验不同研究路径，技能指令不依赖其中的具体基因、疾病或方法顺序：

- [HCC 新辅助 nivolumab 多模态研究](https://doi.org/10.1186/s12943-026-02682-x)
- [小鼠结肠炎突变与空间邻域研究](https://doi.org/10.1038/s41588-026-02673-0)

各版实际输出、语义评分、失败和未运行项见 [验收记录](evals/RESULTS.md)：v0.1.0 两篇真实论文与三份虚构短文；v0.2.0 语言／模式及附件更新；v0.3.0 逐图动作校验与全补图回归；v0.3.1 后续方法解释；v0.3.2 报告与过程记录分离。v0.3.3 为工程与结构修订（入口重写、规则去重、CI、示例重排），未重跑行为试读。局部试读不扩称为真实论文或视觉全流程重跑；完整第三方论文、原图和原始数据不随公开仓库分发。

开发验证：`python3 evals/validate_repo.py` 检查本地链接与记录完整性，`python3 evals/test_reading_record.py` 运行记录回归；两者由 GitHub Actions 在每次推送时自动执行。要用自己的论文做行为评测，按 [PROTOCOL.md](evals/PROTOCOL.md) 进行。视觉夹具可用已有 Pillow 运行 `python3 evals/generate_visual_fixtures.py` 重建；Pillow 仅用于开发夹具，技能阅读不强制 Python 或任何库。

## 来源与许可

本 skill 根据实际阅读需求独立编写，借鉴 `nature-reader` 的原文定位/图文联读思想，以及 `paper-deep-note` 的来源覆盖声明思想。未复制其实现文件或全文翻译工作流，也不依赖这些技能。参考时无法确认所见本地版本的原仓库或许可，因此这里仅注明思想来源，不重分发其内容。

本仓库的原创技能指令、模板、说明及虚构示例采用 [MIT License](LICENSE)。文中链接的第三方论文、数据和工具由各自权利人持有，适用其自身许可；本仓库许可证不延伸至这些外部材料。

## 反馈与贡献

欢迎通过 [Issues](https://github.com/Gaoyuan-0423/biomedical-paper-reader/issues) 提交适配问题或通过 Pull Request 改进。反馈最好包括论文 DOI/公开链接、出现问题的输出、原文定位，以及期望的处理方式；只分享有权公开的材料。规则变更应说明针对哪个证据问题，并更新相应案例或记录，避免为单篇论文写死疾病、基因或技术顺序。

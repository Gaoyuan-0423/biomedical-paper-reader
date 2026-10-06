# biomedical-paper-reader

**单篇生物医学论文精读：看懂实验为什么做，判断证据支持到哪里，提炼可以借鉴的研究设计。**

版本：v0.3.0。默认中文完整精读，支持英文及明确要求的中英双语；另有速读和指定图／方法解析。完整精读逐张概览本地已有主图与补图，按实际证据选择深入核查的面板。

A Chinese-first agent skill for close reading, quick screening, or focused figure/method analysis of one biomedical paper. Explicit English and Chinese–English requests are supported with the same evidence standards.

[Skill 入口](SKILL.md) · [报告模板](references/report-template.md) · [示例](examples/README.md) · [验收记录](evals/RESULTS.md) · [MIT 许可](LICENSE)

## 输入与输出

提供 PDF、DOI、论文链接或原文即可开始，无需先写研究问题。二手解析或部分材料也能处理，但会明确覆盖范围与暂定判断。

仅有短片段且原文无法获取时，合并无实质内容的章节，集中说明证据与关键缺口；完整报告的篇幅来自原文信息量。

**补充材料默认使用本地已有文件。** 检查上传内容、论文同目录和用户指定的相关文件夹；确认属于该论文后，完整精读逐张概览已有补图并读图注，补充表与数据按相关问题读取。没有补充文件时继续正文分析，说明无法核查的主张及受限范围。只有用户明确要求补取时才联网下载，不把附件缺失当作作者未做验证。

**直接复用可读的 PDF 提取结果。** 使用现有工具提取文本、检查阅读顺序并保留页序，不再优先使用 MarkItDown，也不要求先生成 Markdown 底稿。原 PDF 继续用于数字和图像核查。只有具体页面的提取质量影响解读时才更换处理方式。

**范围内的图先概览，必要面板再放大。** 完整精读覆盖已有每张主图、补图及 Extended Data；速读／专题覆盖选定结果及必要对照所在图。读完整图注，关注核心结论、必要对照、阴性结果及图文冲突；已看清内容复用，同组待查面板成组准备。报告区分概览和具体核查范围。执行细节见 [材料处理说明](references/material-handling.md)。

**查看状态可追溯，重大批评须复核。** 使用[逐图记录](references/reading-record.md)汇总范围，转换或复制图片不算查看；图内标签、图注和正文独立对照。先确认样本层级、比较和指标是否相同，再判断冲突。可读图仍待看时继续读取；真实障碍明确标受限交付。可选标准库校验器检查记录与声明，有宿主事件文件时再检查查看动作，不代替科学语义判断。

语言由用户要求决定，英文论文不会自动触发英文输出。未指定就用中文；明确双语时两种表述共用证据记录，数字、方向和判断保持一致。英文直接依据证据写作，标题、解读、局限和建议均使用自然学术英文。

| 模式 | 实际读取与交付 |
|---|---|
| 完整精读（默认） | 通读可用正文和方法，逐张概览已有主图／补图，必要面板深入核查；交付论证与逐图／结果解释，未完成范围标受限 |
| 速读 | 先读摘要、问题、结果结构与讨论，再定点核查筛选判断依据的图、对照及方法；列明未读范围，不声称全文核验 |
| 指定图／方法 | 解析目标并补足影响解释的上下文、必要对照和已有相关补图；交付局部设计、结果、边界及适配建议 |

用户表达明确即可选用，无需填写参数。详见 [模式与语言适配](references/reading-modes.md)。完整精读的写作职责如下，信息简单时允许合并重复章节：

1. 开篇导读：核心发现、看点、阅读建议和适用场景。
2. 文章信息：身份、来源、已读与未获取材料。
3. 研究概述：已有认识、缺口、切入点与总体设计。
4. 全文逻辑路线图：跟随原文证据推进，标注图表。
5. 逐图/逐结果精读：动机、方法、结果、逻辑、边界、原图定位。
6. 研究总结与课题借鉴：贡献、局限、可迁移环节和待验证设计。
7. 核心证据记录及数据入口：来源版本、页码／图号／面板、比较、样本／分母、实际核查方式、冲突和支持边界；资源用途及真实读取状态。

有文件工具时交付独立论文目录下的 `report.md`，必要图像放在 `assets/`；报告内链接保持相对路径。没有文件工具时直接输出文本。

## 使用

### 直接使用

下载或克隆仓库后，在支持文件读取的助手中指定入口：

```bash
git clone https://github.com/Gaoyuan-0423/biomedical-paper-reader.git
```

```text
请使用 ./biomedical-paper-reader/SKILL.md 阅读这份 PDF，生成中文精读报告。
```

路径相对于你的工作目录；必要时改用 `SKILL.md` 的绝对路径。要使用客户端的技能发现功能，将整个目录放入它支持的 skills 目录，保留 `references/`。

### Codex 本地安装

以下适用于目标目录尚不存在的情况：

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/Gaoyuan-0423/biomedical-paper-reader.git ~/.codex/skills/biomedical-paper-reader
```

使用自定义配置目录时，改用其对应的 skills 目录。如果已存在同名版本，先比较内容，保留自己的修改。安装后的发现/刷新行为由客户端决定。识别后可使用：

```text
使用 $biomedical-paper-reader 解析这篇论文：<DOI、链接或附件>。
```

还可以补充研究方向以获得更贴合的迁移建议；未补充时给条件式适用场景。速读会缩小实际阅读范围；专题会补足必要上下文，不靠省略关键对照实现简短。

### 语言与模式调用示例

中文完整精读（也是未指定语言和模式时的默认）：

```text
使用 $biomedical-paper-reader 精读这份 PDF，生成中文报告：<附件>。
```

英文完整精读：

```text
Use $biomedical-paper-reader to produce a full close-reading report in English for <PDF or DOI>, including figure interpretation, limitations, and research suggestions.
```

英文速读：

```text
使用 $biomedical-paper-reader 用英文速读这篇论文，判断是否值得完整精读，并说明实际已读和未读范围：<附件>。
```

指定图英文解析：

```text
Use $biomedical-paper-reader to analyze Figure 3 in English. Read the relevant methods, controls, and surrounding results needed to interpret it: <PDF>.
```

明确双语或专题方法也可直接表达，例如“中英双语解析 Figure 2”或“用英文讲解该文的谱系追踪方法及关键假设”。这些是分析报告，不是全文逐句翻译。

## 工作方式

按所选模式建立并复用原文章节、图表和主张索引，再联读相关正文、Methods、图注及图像。核心结论、引用数字和主要批评的证据记录直接进入输出；作者报告、图像核对、简单复算与原始数据重分析分别说明，不把未执行动作写成已完成。

多队列／嵌套样本记录个体→组织→切片→细胞层级及分析单位，方法不明时标无法确认。关键公开数据按数据集、子集、发现／验证角色和支持图表映射，说明样本重叠是否实际核实。机制按 A→B→C 逐边说明证据、替代解释和缺口；相关检查按需启用。

方法检查点按内容启用，不要求每篇出现空间组学、通讯、谱系或动物实验。无图论文按主表、结果小节或方法单元展开。背景历史、复杂算法、写作表达按相关性决定深度。

依赖宿主提供的文本、文件、PDF/图像及可选联网能力；没有固定 MCP、第三方 skill、Python 库或付费服务要求。全文无法取得、OCR 不清或图像缺失时会标出具体限制。它生成的是证据支持的阅读报告，未实际执行的代码和数据不会被标为已经复现。

## 文件组织

- `SKILL.md`：触发说明、工作流和交付要求。
- `references/report-template.md`：各部分与逐图单元的写作职责。
- `references/reading-modes.md`：三种模式的读取范围、必要核查、输出及语言适配。
- `references/evidence-rules.md`：定位、统计单位、推断范围与缺失信息规则。
- `references/study-checks.md`：按证据类型选用的内部检查点。
- `references/material-handling.md`：本地附件、文本复用、分步看图、下载调度和状态同步。
- `references/reading-record.md`、`scripts/check_reading_record.py`：逐图状态、实际查看引用及覆盖／声明检查；无宿主事件时只校验记录一致性。
- `agents/openai.yaml`：可选的 Codex 界面元数据。
- `evals/cases.md`、`evals/fixtures/`：行为验收设计和原创虚构材料。
- `evals/RESULTS.md`：版本对应的验收入口；历史记录与新回归分开。
- `evals/generate_visual_fixtures.py`、`evals/fixtures/visual/`：可重建的原创多面板图、正文及补充材料；不含第三方论文资源。
- `evals/generate_coverage_fixtures.py`、`evals/fixtures/coverage/`：所有补图初始已在本地的原创回归材料，覆盖漏看、标签误读及真假冲突。
- `evals/test_reading_record.py`：查看动作与覆盖状态的执行回归。
- `evals/validate_repo.py`：使用 Python 标准库检查本地链接、文件引用与回归记录完整性；不替代语义评分。
- `examples/`、`evals/runs/`：历史示例与当前版本实际试读输出、评分及失败记录。
- `CHANGELOG.md`：版本变化。

## 示范与验证

两篇真实试读材料用于检验不同研究路径，技能指令不依赖其中的具体基因、疾病或方法顺序：

- [HCC 新辅助 nivolumab 多模态研究](https://doi.org/10.1186/s12943-026-02682-x)
- [小鼠结肠炎突变与空间邻域研究](https://doi.org/10.1038/s41588-026-02673-0)

既有两篇真实试读和三份虚构短文的行为验收对应 v0.1.0；v0.1.1、v0.1.2 没有重跑，不能把旧结果当作当前版验收。v0.2.0 的语言／模式及附件更新试读保留原版本记录；v0.3.0 增加逐图动作校验及所有补图初始可用的回归材料。各版实际输出、语义评分、失败和未运行项见 [验收记录](evals/RESULTS.md)。可查看 [输入与报告示例](examples/README.md)。完整第三方论文、原图和原始数据不随公开仓库分发。

开发验收可执行 `python3 evals/validate_repo.py`；视觉夹具可用已有 Pillow 运行 `python3 evals/generate_visual_fixtures.py` 重建。Pillow 仅用于开发夹具，技能阅读不强制 Python 或任何库。模型行为由独立试读和逐项来源复核评估，不能只靠关键词、标题或静态脚本判通过。

## 来源与许可

本 skill 根据实际阅读需求独立编写，借鉴 `nature-reader` 的原文定位/图文联读思想，以及 `paper-deep-note` 的来源覆盖声明思想。未复制其实现文件或全文翻译工作流，也不依赖这些技能。参考时无法确认所见本地版本的原仓库或许可，因此这里仅注明思想来源，不重分发其内容。

本仓库的原创技能指令、模板、说明及虚构示例采用 [MIT License](LICENSE)。文中链接的第三方论文、数据和工具由各自权利人持有，适用其自身许可；本仓库许可证不延伸至这些外部材料。

## 反馈与贡献

欢迎通过 [Issues](https://github.com/Gaoyuan-0423/biomedical-paper-reader/issues) 提交适配问题或通过 Pull Request 改进。反馈最好包括论文 DOI/公开链接、出现问题的输出、原文定位，以及期望的处理方式；只分享有权公开的材料。规则变更应说明针对哪个证据问题，并更新相应案例或记录，避免为单篇论文写死疾病、基因或技术顺序。

# biomedical-paper-reader

**单篇生物医学论文精读：看懂实验为什么做，判断证据支持到哪里，提炼可以借鉴的研究设计。**

版本：v0.1.0。默认中文，保留必要英文术语。面向原创生物医学研究；临床、动物、机制、组学、空间、谱系或方法论文按实际证据选择检查点。

A Chinese-first agent skill for reading one biomedical paper at a time: follow its figures and argument, assess evidence, and develop testable research ideas.

[Skill 入口](SKILL.md) · [报告模板](references/report-template.md) · [示例](examples/README.md) · [验收记录](evals/RESULTS.md) · [MIT 许可](LICENSE)

## 输入与输出

提供 PDF、DOI、论文链接或原文即可开始，无需先写研究问题。二手解析或部分材料也能处理，但会明确覆盖范围与暂定判断。

仅有短片段且原文无法获取时，合并无实质内容的章节，集中说明证据与关键缺口；完整报告的篇幅来自原文信息量。

默认报告顺序：

1. 开篇导读：核心发现、看点、阅读建议和适用场景。
2. 文章信息：身份、来源、已读与未获取材料。
3. 研究概述：已有认识、缺口、切入点与总体设计。
4. 全文逻辑路线图：跟随原文证据推进，标注图表。
5. 逐图/逐结果精读：动机、方法、结果、逻辑、边界、原图定位。
6. 研究总结与课题借鉴：贡献、局限、可迁移环节和待验证设计。
7. 数据与复现入口：资源用途、实际获取状态和关键缺口。

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

还可以补充研究方向以获得更贴合的迁移建议；未补充时给条件式适用场景。用户要求速读时压缩篇幅，要求专题方法解析时加深对应部分。

## 工作方式

先建立原文章节、图表和主张索引，再核对正文、Methods、图注及实际图像，最后写阅读判断与研究建议。独立个体数与细胞/切片数、观察与因果、作者解释与新增假设都分别处理。

方法检查点按内容启用，不要求每篇出现空间组学、通讯、谱系或动物实验。无图论文按主表、结果小节或方法单元展开。背景历史、复杂算法、写作表达按相关性决定深度。

依赖宿主提供的文本、文件、PDF/图像及可选联网能力；没有固定 MCP、第三方 skill、Python 库或付费服务要求。全文无法取得、OCR 不清或图像缺失时会标出具体限制。它生成的是证据支持的阅读报告，未实际执行的代码和数据不会被标为已经复现。

## 文件组织

- `SKILL.md`：触发说明、工作流和交付要求。
- `references/report-template.md`：各部分与逐图单元的写作职责。
- `references/evidence-rules.md`：定位、统计单位、推断范围与缺失信息规则。
- `references/study-checks.md`：按证据类型选用的内部检查点。
- `agents/openai.yaml`：可选的 Codex 界面元数据。
- `evals/cases.md`、`evals/fixtures/`：行为验收设计和原创虚构材料。
- `evals/RESULTS.md`：本轮真实验收记录与限制。
- `examples/`：三份原创虚构材料的实际输出，便于预览报告风格。
- `CHANGELOG.md`：版本变化。

## 示范与验证

两篇真实试读材料用于检验不同研究路径，技能指令不依赖其中的具体基因、疾病或方法顺序：

- [HCC 新辅助 nivolumab 多模态研究](https://doi.org/10.1186/s12943-026-02682-x)
- [小鼠结肠炎突变与空间邻域研究](https://doi.org/10.1038/s41588-026-02673-0)

另外使用三份明确标注为虚构的短材料检查来源缺失、观察证据与计算预测的适配能力。可查看[输入与实际报告](examples/README.md)。验收关注真实行为，而非只检查标题或关键词，结果见[验收记录](evals/RESULTS.md)。两份真实论文完整试读作为本地验收材料保留；公开仓库提供论文入口和验收结论，完整论文、原图及原始数据不随仓库分发。

## 来源与许可

本 skill 根据实际阅读需求独立编写，借鉴 `nature-reader` 的原文定位/图文联读思想，以及 `paper-deep-note` 的来源覆盖声明思想。未复制其实现文件或全文翻译工作流，也不依赖这些技能。参考时无法确认所见本地版本的原仓库或许可，因此这里仅注明思想来源，不重分发其内容。

本仓库的原创技能指令、模板、说明及虚构示例采用 [MIT License](LICENSE)。文中链接的第三方论文、数据和工具由各自权利人持有，适用其自身许可；本仓库许可证不延伸至这些外部材料。

## 反馈与贡献

欢迎通过 [Issues](https://github.com/Gaoyuan-0423/biomedical-paper-reader/issues) 提交适配问题或通过 Pull Request 改进。反馈最好包括论文 DOI/公开链接、出现问题的输出、原文定位，以及期望的处理方式；只分享有权公开的材料。规则变更应说明针对哪个证据问题，并更新相应案例或记录，避免为单篇论文写死疾病、基因或技术顺序。

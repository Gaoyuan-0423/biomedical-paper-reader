# v0.2.0 实际验收 — 2026-10-02

基线为经用户 HTTP 代理获取确认的 `9b5e89725e562777a4eca20d8409adc0fdcf9aaf`（v0.1.2），开始时本地工作区干净。修改在本地 `codex/evidence-modes-v0.2.0` 分支；本记录不表示已发布。

本轮使用原创合成文字和多面板图，不捆绑第三方论文或真实患者数据。初始三组新试读者及后续一位独立方法复验者仅接收技能、隔离输入和用户请求，不接收 golden facts、案例答案或旧报告；部分案例由同一读者批次完成。主文阶段隔离补充文件，保存原报告后再按新增材料请求更新。评分者实际查看图像、联读原文与输出，逐项核对科学判断与动作；脚本仅检查文件结构与完整性。

## 输入、输出与判定

| 案例 | 输入／任务 | 实际输出与评分 | 当前结论 |
|---|---|---|---|
| 默认中文短材料 | [输入](legacy-incomplete/input/incomplete.md) · [任务](legacy-incomplete/request.txt) | [报告](legacy-incomplete/report.md) · [评分](legacy-incomplete/grading.json) | 通过：二手来源、未知方法、文档内指令和样本单位处理准确 |
| 英文完整精读 | [输入](english-full/input/observational.md) · [任务](english-full/request.txt) | [报告](english-full/report.md) · [评分](english-full/grading.json) | 通过：自然英文、18/72/18,000、跨零区间、年龄混杂与真实复算 |
| 中文方法精读 | [输入](legacy-method/input/prediction-method.md) · [任务](legacy-method/request.txt) | [报告](legacy-method/report.md) · [评分](legacy-method/grading.json) | 通过：真实结果顺序、特征选择泄漏、供者重叠与证据记录 |
| 中文视觉精读与补充更新 | [主文](visual-update/input/main.md) · [补充](visual-update/input/supplement.md) · [新增任务](visual-update/request-update.txt) | [主阶段](visual-update/report-before.md) · [更新后](visual-update/report.md) · [评分](visual-update/grading.json) | 通过：方向/小数点冲突、必要对照、低清限制；新增后更新供者单位、跨零CI、KO残留响应和4/8重叠 |
| 英文速读，短单图 | [输入](english-fast/input/main.md) · [任务](english-fast/request.txt) | [报告](english-fast/report.md) · [评分](english-fast/grading.json) | **部分通过**：科学与范围诚实，实际读取很广；本输入不足以展示多结果选择性，不宣称效率通过 |
| 英文速读，多结果 | [输入](english-fast-selective/input/quick-screening.md) · [任务](english-fast-selective/request.txt) | [报告](english-fast-selective/report.md) · [评分](english-fast-selective/grading.json) | 通过：实际读取供者关联与方法，工程结果/方法正文未读且未背书 |
| 英文指定图 | [任务](english-focus/request.txt) | [报告](english-focus/report.md) · [评分](english-focus/grading.json) | 通过：B–D及必要上下文；未冒称完整机制或所有面板已核验 |
| 双语指定图 | [任务](bilingual-focus/request.txt) | [报告](bilingual-focus/report.md) · [评分](bilingual-focus/grading.json) | 通过：数字、方向、否定和不确定性一致，共用证据记录 |
| 英文方法专题 | [任务](english-method-focus/request.txt) · [输入](english-method-focus/input/prediction-method.md) | [首次输出](english-method-focus/report-first.md) · [首次评分](english-method-focus/grading-first.json) · [复验输出](english-method-focus/report.md) · [复验评分](english-method-focus/grading.json) | **修订后复验通过**：首次操作表顺序有局部错误，失败记录保留；增加相关检查点后由新的独立读者复验 |

本轮共 9 个案例、11 份实际报告（含补充加入前后及方法首次／复验）：当前 8 个通过、1 个部分通过。首次方法错误单独保留，不以当前通过倒改历史。

每个目录保存读取范围 `trace*.json`、实际 UTC `execution*.json`、输入、请求及评分。证据依据在评分文件中指向具体输出行和源位置；不是自动匹配词语后评分。主图的 0.08 并未转录到原图注，因此文字阅读不能单独完成这一项视觉验收。

## 独立审阅与失败保留

- [三份短文审阅](independent-review.json)：核对单位、推断、语言、动作与证据记录。
- [主图与初次速读审阅](independent-visual-main-review.json)：实际查看主图，保留速读选择性证据不足的判定。
- [英文／双语专题审阅](independent-focused-review.json)：复用同版已看清原图，逐段比对双语数字和支持边界。
- [补充更新、方法与新增速读审阅](independent-final-review.json)：实际查看新增补图并核对更新；方法首次错误保留在此审阅及首次评分中。
- [修订后方法独立复验](independent-method-retest-review.json)：核对新的隔离试读，确认已知／未知计算顺序与原有泄漏分别表述。
- [失败、部分通过与复验记录](FAILURES.md)。首次输出没有因修订而被覆盖。

原始病例材料未改动；日志导出仅把临时绝对路径替换为可移植别名。`repo:` 相对仓库根，`run:` 相对此目录，`input/` 相对案例目录。前三份短文报告各移除一行文末空行，原始／导出哈希在 manifest 中保存；正文与评分行号未改变。UTC 值、报告分析及读取动作没有通过路径整理或空行处理改写。文件 SHA256、试读指令版本与案例清单见 [manifest.json](manifest.json)。不同试读阶段的指令哈希分别保存，不能把旧输出假称为在同一最终字节版本上重跑。算法顺序规则只影响方法重建，针对英文专题复验；视觉和模式指令未改变，其他案例保留原运行结果。

## 可执行检查与边界

从仓库根运行：

```bash
python3 evals/validate_repo.py --self-test
python3 evals/validate_repo.py
python3 evals/generate_visual_fixtures.py
git diff --check
```

官方 Skill Creator `quick_validate.py` 也实际运行，PyYAML 安装在临时开发目录；它不是技能运行依赖。Pillow 只用于重建原创视觉夹具。命令、退出状态及输出见 [validation-log.json](validation-log.json)。两张 PNG 实际查看和解码；同环境重建可重复，跨平台字体下字节一致性未验证。

本轮**没有**重跑 v0.1.0 的两篇真实论文、重分析任何原始数据、测量跨客户端速度、运行 Wisp/DeepSeekHarness 或验证其计划 UI。读者 trace 是动作摘要，独立审阅可核对一致性，不等同完整宿主逐调用日志；部分任务共享批次时钟，另有主阶段在预读后才开始捕获时间，不能以这些耗时直接比较模式速度。速读多结果案例只证明该输入上的范围选择，不构成真实长 PDF 的提速保证。

科学语义有限案例通过不代表全部生物医学论文可靠。结构检查通过也不把未读材料、作者统计或未执行复现升级为已验证。

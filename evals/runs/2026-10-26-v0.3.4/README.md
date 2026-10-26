# v0.3.4 维护验证记录

日期：2026-10-26。范围：GitHub Actions 运行环境、自动更新配置与发布元数据。阅读行为要求未改变；`SKILL.md` 仅将版本标识从 v0.3.3 改为 v0.3.4，`references/` 指令保持不变。

## 实际执行的维护检查

- `python3 evals/test_reading_record.py -v`：15 项记录回归通过。
- `python3 evals/test_prepare_release.py -v`：10 项发布元数据回归通过。
- `python3 scripts/prepare_release.py v0.3.4 --output /tmp/biomedical-v0.3.4-notes.md`：版本与中英文发布说明一致性检查通过。
- `python3 evals/validate_repo.py`：本地链接与最新维护 manifest 的指令哈希检查通过。
- 三个 `scripts/` Python 辅助脚本编译、工作流与 Dependabot YAML 解析及 `git diff --check` 通过。
- 新版 Action 配置已在 [PR #5](https://github.com/Gaoyuan-0423/biomedical-paper-reader/pull/5) 的 GitHub 托管 CI 中通过，旧 Node.js 20 警告及 Ubuntu 迁移提示未再出现。具体提交与检查结果以该 PR 的运行记录为准。

## 未执行与结果边界

本版未重新运行真实论文、视觉材料、语言／模式案例、App 端到端测试或原始数据分析。以上维护检查不构成行为准确率、阅读质量或性能验证。历史记录未改写；当前指令哈希保存于本目录 [manifest](manifest.json)，`cases` 为空，表示没有新的论文试读产物。

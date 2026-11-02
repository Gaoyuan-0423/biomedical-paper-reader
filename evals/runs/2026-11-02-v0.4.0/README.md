# v0.4.0 桌面预览验证记录

日期：2026-11-02。范围：BioReader macOS 桌面预览、版本资料与发布说明。论文阅读行为要求未改动；`SKILL.md` 仅将版本标识从 v0.3.4 改为 v0.4.0，`references/` 指令保持不变。

## 实际执行的检查

- `python evals/test_reading_record.py -v`：15 项阅读记录回归通过。
- `python evals/test_prepare_release.py -v`：10 项发布元数据回归通过。
- `python -m unittest discover -s app/tests -v`：4 项桌面服务与存储回归通过。
- `python scripts/prepare_release.py v0.4.0`：中英文 README、SKILL、CHANGELOG 与发布说明的版本一致性检查通过。
- `python evals/validate_repo.py`：在仅包含源码的临时副本中检查本地链接与本目录的指令哈希，通过。构建输出和虚拟环境不属于发布源码；本机 `app/dist/` 中的第三方打包许可证含上游相对链接，未将这些生成文件纳入源码链接验收。
- `git diff --check`：通过。
- 桌面实现合并到 `main` 后的 [GitHub CI](https://github.com/Gaoyuan-0423/biomedical-paper-reader/actions/runs/41474266310) 全部通过；本次发布资料未改变应用运行代码。
- 本机实际 arm64 应用包由提交 `e976a0eb250be7a61e9dfae66059e7f9b664e4b9` 构建，本次版本资料更新未改变该应用代码。应用包完成 `codesign --verify --deep --strict`、Info.plist 检查与启动检查；在临时数据目录中通过本地 PDF.js／字体／docx 资源加载、Cookie／Host／Origin 限制、PDF 上传／下载／删除及笔记持久化检查。分发 ZIP 解压后复核应用包完整性通过。

## 未执行与结果边界

窗口内 PDF 视觉显示、macOS 保存对话框、Word 导出和真实 AI 生成仍待人工验收，由 [Issue #6](https://github.com/Gaoyuan-0423/biomedical-paper-reader/issues/6) 跟踪。应用包完整性检查不代表 Apple 开发者签名、公证或其他 Mac 的安装兼容性；本次包仅为 arm64 桌面预览。

未重跑真实论文、视觉材料或语言／模式行为试读，不新增阅读准确率或性能结论。历史记录不改写；当前指令哈希保存于本目录 [manifest](manifest.json)，`cases` 为空，表示没有新的论文试读产物。

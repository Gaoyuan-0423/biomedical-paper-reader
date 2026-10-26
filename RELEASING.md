# 发布维护 / Release maintenance

[中文](#中文) · [English](#english)

## 中文

### 为什么 README 比 Releases 新

`main` 上的 README、SKILL 和 CHANGELOG 描述当前源码版本；GitHub Releases 记录单独发布的标签快照。修改版本文字、合并 PR 或通过 CI 都不会自动创建 Release。2026-10-26 补发前核查时，文档已到 v0.3.3，但最新正式发布及标签仍为 v0.2.0；此前仅有检查工作流，没有发布工作流。v0.3.3 现已正式发布。

### 发布新版本

以下以 v0.3.4 为例；后续发布应替换为准备好的新版本号，不能重复发布已有版本。

1. 审阅 [v0.3.4 中英文发布说明](releases/v0.3.4.md)，确认待发布改动、版本资料与验证记录一致，并将对应 PR 合并到 `main`。
2. 打开仓库 **Actions → Publish release → Run workflow**，选择 **main**，输入版本 **v0.3.4**。也可以在仓库目录运行：

   ```bash
   gh workflow run release.yml --ref main -f version=v0.3.4
   ```

3. 检查该次运行是否成功，再到 [Releases](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases) 确认 v0.3.4 和 Latest 标记。标签绑定触发运行时的 `main` 提交；运行期间的新提交不包含在该版本中。

合并 PR 本身不执行发布；其他分支上的运行会跳过发布任务。工作流只接受 `vX.Y.Z` 稳定版本，先检查 README 中英文、SKILL、CHANGELOG 与发布说明一致，再运行仓库完整性和记录回归检查，最后创建标签及正式 Release。新发布使用新的稳定版本号，不覆盖已有发布，也不补造历史发布日期。

### 后续版本

- 在同一个 PR 中更新 `SKILL.md` 版本声明、README 中英文版本和 CHANGELOG 的最新正式版本章节，并添加 `releases/vX.Y.Z.md`。发布说明以 `# vX.Y.Z` 开头，包含 `## 中文` 与 `## English`，如实记录验证范围；比较基准使用上一次实际发布的版本。
- 发布说明中的仓库文件链接使用带目标标签的完整 GitHub URL；不要使用在 Release 页面无法定位的相对文件链接。
- 合并前执行以下检查（将示例版本替换为目标版本）；版本验证脚本只读取本地资料并输出发布说明，不联网、不创建标签。

  ```bash
  python3 scripts/prepare_release.py v0.3.4 --output /tmp/biomedical-release-notes.md
  python3 evals/test_prepare_release.py -v
  python3 evals/validate_repo.py
  python3 evals/test_reading_record.py -v
  ```

- 合并后按上述步骤手动发布。工作流使用 GitHub 提供的 `GITHUB_TOKEN` 和 `contents: write` 权限；通常无需另建个人令牌。若仓库规则禁止 Actions 创建标签或 Release，运行会失败并保留日志，需要维护者处理相应权限。

### 自动维护 GitHub Actions

[Dependabot 配置](.github/dependabot.yml)合并到默认分支后，GitHub 将按每周一北京时间 09:00 的计划检查工作流引用的 Action，发现新版后自动创建更新 PR，由现有 CI 检查，维护者审阅后合并。Action 继续使用完整提交 SHA 固定，并在行尾标注对应版本。这项服务在 GitHub 上运行，不需要 Codex 持续在线，也不会自动合并 PR 或发布新版本。

此配置只维护 Action 引用；`runs-on` 的 Ubuntu 镜像和 `python-version` 不由它升级。CI 与发布任务固定使用 `ubuntu-24.04`，运行环境升级需另开 PR 验证。已发布的 v0.3.3 保留原标签快照，这类工作流维护不需要重复发布同一版本。

### 重试与现有标签

已有 Release（包括草稿）不会被覆盖；已有标签仅在其最终指向的提交与此次检查的提交相同时允许继续，注释标签同样适用。若创建标签后发布失败，使用原运行的 **Re-run failed jobs**，以保留同一个提交。若从新的 `main` 提交另起运行而旧标签已经存在，工作流会拒绝目标不一致；不要移动已发布标签来规避此检查，应核实原运行或准备新版本。所有发布运行串行执行。

## English

### Why the README can be newer than Releases

README, SKILL, and CHANGELOG on `main` describe the current source version; GitHub Releases are separately published tag snapshots. Editing version text, merging a PR, or passing CI does not create a Release. Before the catch-up release on 2026-10-26, the documentation was at v0.3.3 while the latest release and tag were still v0.2.0. The repository previously had a checking workflow but no publishing workflow. v0.3.3 has since been published.

### Publish a new version

The following example uses v0.3.4. For future releases, substitute the prepared new version; existing versions cannot be published again.

1. Review the [bilingual v0.3.4 release notes](releases/v0.3.4.md), confirm that changes, version metadata, and validation records agree, and merge the corresponding PR into `main`.
2. Open **Actions → Publish release → Run workflow**, select **main**, and enter **v0.3.4**. Alternatively, run this from the repository:

   ```bash
   gh workflow run release.yml --ref main -f version=v0.3.4
   ```

3. Confirm that the run succeeds and that [Releases](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases) shows v0.3.4 as Latest. The tag points to the `main` commit captured when the run was triggered, excluding commits added during the run.

Merging the PR does not publish a release. Runs on other branches skip the publishing job. The workflow accepts stable `vX.Y.Z` versions only, checks both README versions, SKILL, CHANGELOG, and release notes, then runs repository integrity and reading-record regression checks before creating the tag and public Release. New releases use new stable version numbers; they do not overwrite existing releases or invent historical publication dates.

### Future versions

- In one PR, update the version declaration in `SKILL.md`, both README version declarations, and the latest numbered CHANGELOG section, and add `releases/vX.Y.Z.md`. Notes must start with `# vX.Y.Z`, contain `## 中文` and `## English`, accurately describe validation coverage, and compare against the last actually published release.
- Use full GitHub URLs containing the target tag for repository files linked in release notes; relative file links may not resolve on the Release page.
- Before merging, run the four commands in the Chinese section above, replacing the example version. The version checker only reads local files and outputs notes; it does not access the network or create tags.
- After merging, publish manually as described above. The workflow uses GitHub's `GITHUB_TOKEN` with `contents: write`; a separate personal token is normally unnecessary. Repository rules that disallow tag or Release creation by Actions will cause the run to fail with logs for maintainers to investigate.

### Automatic GitHub Actions maintenance

Once the [Dependabot configuration](.github/dependabot.yml) is merged into the default branch, GitHub checks workflow Action references on a weekly schedule, Mondays at 09:00 Asia/Shanghai. It opens update PRs when newer versions are available; the existing CI checks them, and maintainers review and merge them. Actions remain pinned to full commit SHAs with corresponding version comments. This service runs on GitHub and does not require Codex to remain online. It does not automatically merge PRs or publish releases.

This configuration updates Action references only, not the Ubuntu image in `runs-on` or `python-version`. CI and release jobs use `ubuntu-24.04`; runtime upgrades require a separate PR and validation. The published v0.3.3 tag remains its original snapshot; workflow maintenance does not require publishing the same version again.

### Retries and existing tags

Existing Releases, including drafts, are never overwritten. An existing tag is accepted only if it resolves to the same commit that this run checked; annotated tags are supported. If publication fails after tag creation, use **Re-run failed jobs** on the original run to preserve its commit. A new run from a newer `main` commit will reject an existing tag pointing elsewhere. Do not move published tags to bypass this check; inspect the original run or prepare a new version. Release runs are serialized.

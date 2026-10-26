# 发布维护 / Release maintenance

[中文](#中文) · [English](#english)

## 中文

### 为什么 README 比 Releases 新

`main` 上的 README、SKILL 和 CHANGELOG 描述当前源码版本；GitHub Releases 记录单独发布的标签快照。修改版本文字、合并 PR 或通过 CI 都不会自动创建 Release。2026-10-26 核查时，文档已到 v0.3.3，但最新正式发布及标签仍为 v0.2.0；此前仅有检查工作流，没有发布工作流。

### 补发 v0.3.3

1. 将包含发布工作流的 PR 合并到 `main`。先审阅 [v0.3.3 中英文发布说明](releases/v0.3.3.md)，其中包含自 v0.2.0 以来的累计变化，以及后续加入当前源码的 App 和双语 README。
2. 打开仓库 **Actions → Publish release → Run workflow**，选择 **main**，输入版本 **v0.3.3**。也可以在仓库目录运行：

   ```bash
   gh workflow run release.yml --ref main -f version=v0.3.3
   ```

3. 检查该次运行是否成功，再到 [Releases](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases) 确认 v0.3.3 和 Latest 标记。标签绑定触发运行时的 `main` 提交；运行期间的新提交不包含在该版本中。

合并 PR 本身不执行发布；其他分支上的运行会跳过发布任务。工作流只接受 `vX.Y.Z` 稳定版本，先检查 README 中英文、SKILL、CHANGELOG 与发布说明一致，再运行仓库完整性和记录回归检查，最后创建标签及正式 Release。此次补发使用现有 v0.3.3 版本号，不重写 v0.2.0，也不为未单独发布的中间版本补造历史发布日期。

### 后续版本

- 在同一个 PR 中更新 `SKILL.md` 版本声明、README 中英文版本和 CHANGELOG 的最新正式版本章节，并添加 `releases/vX.Y.Z.md`。发布说明以 `# vX.Y.Z` 开头，包含 `## 中文` 与 `## English`，如实记录验证范围；比较基准使用上一次实际发布的版本。
- 发布说明中的仓库文件链接使用带目标标签的完整 GitHub URL；不要使用在 Release 页面无法定位的相对文件链接。
- 合并前执行以下检查（将示例版本替换为目标版本）；版本验证脚本只读取本地资料并输出发布说明，不联网、不创建标签。

  ```bash
  python3 scripts/prepare_release.py v0.3.3 --output /tmp/biomedical-release-notes.md
  python3 evals/test_prepare_release.py -v
  python3 evals/validate_repo.py
  python3 evals/test_reading_record.py -v
  ```

- 合并后按上述步骤手动发布。工作流使用 GitHub 提供的 `GITHUB_TOKEN` 和 `contents: write` 权限；通常无需另建个人令牌。若仓库规则禁止 Actions 创建标签或 Release，运行会失败并保留日志，需要维护者处理相应权限。

### 重试与现有标签

已有 Release（包括草稿）不会被覆盖；已有标签仅在其最终指向的提交与此次检查的提交相同时允许继续，注释标签同样适用。若创建标签后发布失败，使用原运行的 **Re-run failed jobs**，以保留同一个提交。若从新的 `main` 提交另起运行而旧标签已经存在，工作流会拒绝目标不一致；不要移动已发布标签来规避此检查，应核实原运行或准备新版本。所有发布运行串行执行。

## English

### Why the README can be newer than Releases

README, SKILL, and CHANGELOG on `main` describe the current source version; GitHub Releases are separately published tag snapshots. Editing version text, merging a PR, or passing CI does not create a Release. On 2026-10-26, the documentation was at v0.3.3 while the latest release and tag were still v0.2.0. The repository previously had a checking workflow but no publishing workflow.

### Publish the missing v0.3.3 release

1. Merge the PR containing the release workflow into `main`. Review the [bilingual v0.3.3 release notes](releases/v0.3.3.md), which cover cumulative changes since v0.2.0 and the App and bilingual README subsequently added to the current source.
2. Open **Actions → Publish release → Run workflow**, select **main**, and enter **v0.3.3**. Alternatively, run this from the repository:

   ```bash
   gh workflow run release.yml --ref main -f version=v0.3.3
   ```

3. Confirm that the run succeeds and that [Releases](https://github.com/Gaoyuan-0423/biomedical-paper-reader/releases) shows v0.3.3 as Latest. The tag points to the `main` commit captured when the run was triggered, excluding commits added during the run.

Merging the PR does not publish a release. Runs on other branches skip the publishing job. The workflow accepts stable `vX.Y.Z` versions only, checks both README versions, SKILL, CHANGELOG, and release notes, then runs repository integrity and reading-record regression checks before creating the tag and public Release. This catch-up release uses the existing v0.3.3 version; it does not rewrite v0.2.0 or invent historical publication dates for intermediate versions.

### Future versions

- In one PR, update the version declaration in `SKILL.md`, both README version declarations, and the latest numbered CHANGELOG section, and add `releases/vX.Y.Z.md`. Notes must start with `# vX.Y.Z`, contain `## 中文` and `## English`, accurately describe validation coverage, and compare against the last actually published release.
- Use full GitHub URLs containing the target tag for repository files linked in release notes; relative file links may not resolve on the Release page.
- Before merging, run the four commands in the Chinese section above, replacing the example version. The version checker only reads local files and outputs notes; it does not access the network or create tags.
- After merging, publish manually as described above. The workflow uses GitHub's `GITHUB_TOKEN` with `contents: write`; a separate personal token is normally unnecessary. Repository rules that disallow tag or Release creation by Actions will cause the run to fail with logs for maintainers to investigate.

### Retries and existing tags

Existing Releases, including drafts, are never overwritten. An existing tag is accepted only if it resolves to the same commit that this run checked; annotated tags are supported. If publication fails after tag creation, use **Re-run failed jobs** on the original run to preserve its commit. A new run from a newer `main` commit will reject an existing tag pointing elsewhere. Do not move published tags to bypass this check; inspect the original run or prepare a new version. Release runs are serialized.

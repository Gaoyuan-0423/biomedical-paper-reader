# 全库检索基准

在仓库根目录用 BioReader 的 Python 环境执行以下命令。脚本生成临时文献库和 PDF，测量完整的 `GET /api/library/search` 请求，并核对响应内容。

```sh
app/.desktop-venv/bin/python evals/benchmarks/library_search.py
```

加上 `--rust` 可比较 Rust 原型。需要 PATH 中有适用于本机的 `rustc`。脚本将动态库编译到临时目录，由 Python 通过 ctypes 调用。BioReader 的运行与桌面包构建仅使用标准库实现。

```sh
app/.desktop-venv/bin/python evals/benchmarks/library_search.py --rust
```

5 篇和 128 篇文献用于已有文字缓存的查询。脚本分别搜索整库没有的词、最后一篇里的唯一标记和能迅速填满一页结果的常见词。每种组合预热一次，随机交错测量七次。首次提取另用 2 篇文献，每次删除合成库的文字块缓存后测量，共三轮。首次提取保留操作系统文件缓存。版本、平台、测量边界与一次运行的结果见[检索验证记录](../../app/tests/README.md)。

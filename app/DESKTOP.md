# BioReader macOS 桌面版

桌面版在独立窗口运行，关闭窗口会停止本地后台服务。PDF、笔记和设置默认保存在 `~/Library/Application Support/BioReader/`，不会随替换 App 一同删除。现有网页版的 `app/data/` 不会自动迁移；请先备份后再手动复制其中的内容到这个目录。

## 在 Mac 上构建

需要 Python 3.10 或更新版本以及网络连接，以便首次安装构建依赖。在项目根目录运行：

```bash
./app/build-macos.sh
```

如默认 `python3` 版本过旧，可指定已有的 Python：

```bash
BIOREADER_PYTHON=/path/to/python3 ./app/build-macos.sh
```

构建完成后双击 `app/dist/BioReader.app`。该脚本创建独立虚拟环境，打包当前机器的架构，构建结果不会自动上传或发布。

这个构建默认没有 Apple 开发者签名和公证。其他 Mac 可能阻止打开；若要对外分发，需要完成相应签名、公证与安装测试。

界面依赖已随程序提供；AI 模型调用、DOI 查询和相关文献检索仍需网络。AI 笔记需要在设置中配置可用的模型接口及 API Key；Key 以明文保存在本机设置文件中。导出 Markdown 或 Word 时，系统会显示保存文件对话框。


## 验证与限制

这是桌面版预览源码，尚未发布正式安装包。应用内部使用系统 WebKit 和本机后台，不依赖外部浏览器；不等于纯原生界面或完全离线 AI。

本地已完成 Apple 芯片 Mac 的构建与应用包完整性检查、首页及本地资源加载检查，以及会话限制与 PDF 导入／读取／删除接口测试。图形界面自动检查曾超时，PDF 视觉显示、macOS 保存对话框、Word 导出和真实 AI 生成仍需人工验收。CI 只执行服务与存储回归，不代表桌面 GUI 验收。

运行测试：

```bash
python -m pip install -r app/backend/requirements.txt 'pywebview>=6.2.1,<7'
python -m unittest discover -s app/tests -v
```

打包结果由构建机器的架构决定；当前本地试用包为 arm64。应用包、虚拟环境和个人论文库不提交到仓库。

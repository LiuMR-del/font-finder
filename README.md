# font-finder · 字体速查

粘贴一张截图，找出图里用的是哪个字体。为定制商品（名字、球衣号码、婚礼卡片等）的设计流程而做，在 macOS 本机运行，截图不会上传。

*Paste a screenshot, find the font. Runs locally on macOS (Vision OCR), ~1,300 fonts indexed out of the box.*

## 能做什么
- **截图找字**：⌘V 粘贴截图 → 自动框选并识别文字 → 和本地约 1,300 个字体逐一比对，按“很像 / 可能 / 不像”排序，并用截图的颜色预览。支持花背景、描边贴纸字、两行文字。
- **本地没有时**：程序会把文字抠成干净的黑白图，一键复制并打开 WhatTheFont / WhatFontIs / Font Squirrel，在网站上按 ⌘V 粘贴即可，不用注册。
- **样张墙**：没有截图、只有文字时，把文字套用到所有字体上预览，可按粗细、风格、授权筛选，还能对比、收藏、一键安装。

## 安装和启动
需要 macOS，并安装 Xcode 命令行工具（终端运行 `xcode-select --install`，用于 Python 和文字识别）。

```bash
git clone https://github.com/LiuMR-del/font-finder.git
```

然后双击 **`启动字体库.command`**。第一次运行会自动创建 Python 环境、安装依赖、扫描字体、生成字形图集，大约 2–3 分钟；之后几秒内就能打开。
如果 macOS 提示无法打开，请在访达里右键 → 打开。

详细用法见 [使用说明.md](使用说明.md)。

## 字体与授权（请务必阅读）
仓库里的字体分三类，程序里每张字体卡片都会标出所属类别，左侧“授权”栏可以筛选：

| 类别 | 位置 | 数量 | 能否商用 |
|---|---|---|---|
| Google Fonts（装饰体、手写体） | `google-fonts/` | 716 个家族 | ✅ OFL / Apache，可商用，可一键安装 |
| 其他开源字体 | `fonts/` | 178 个文件 | ✅ OFL 等，可商用，可一键安装 |
| **仅供识别**（系统字体、商业字体、仅限个人使用的字体） | `refs/` | 374 个字形 | ❌ **不含字体文件** |

“仅供识别”类只附带少量字符的低分辨率字形图，用来在截图里认出这些字体，**不是可安装的字体**。识别出来后，请点“去获取 ↗”找到原作者或字体公司，**商用前自行购买授权**；系统字体请使用自己电脑上已授权的版本。

用于定制商品属于商业用途。标有 ⚠个人使用 的字体，商用前必须购买授权。

## 添加自己的字体
- 把字体文件或 zip 放进 `新下载字体/` 文件夹，程序运行时约 10 秒自动入库。
- 想加入整个文件夹，新建 `config.local.json`（不会被提交到 git）：
  ```json
  { "sources": ["/Users/你的名字/Fonts"] }
  ```
  然后双击 `重新扫描字体.command`。你自己的字体文件会替代同名字体的“仅供识别”数据。

## 维护者
- 更新 Google Fonts：`.venv/bin/python -m fontfinder.gfonts`
- 从本机字体库重新导出 `fonts/` 和 `refs/`：`.venv/bin/python -m fontfinder.refs`
- 命中率实验：`.venv/bin/python -m fontfinder.bench 50`（加 `script` 只测手写体）

## 许可
程序代码使用 [MIT](LICENSE) 许可。字体各自保留原授权，见上表。

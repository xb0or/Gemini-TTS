# Gemini TTS 工具集

## 简介
Gemini TTS 工具集提供了一套围绕 Google Gemini Text-to-Speech 服务的 Python 实用脚本：
- `igtts.py` 封装了配置管理、声线刷新、文本转语音等核心函数。
- `igtts_gui.py` 基于 Tkinter 提供桌面图形界面，支持单次输入或批量任务。

所有运行时配置存储在同目录下的 `config.json` 中，可通过 GUI 或手动编辑进行管理。

## 功能特点
- 支持 Gemini 预设声线的文本转语音，生成 24 kHz 单声道 WAV 文件。
- 图形界面支持单条生成、批量队列、任务导入导出与进度日志。
- 自动管理配置与日志，允许设置默认输出路径、批量延时和调试级别。
- 可调用 Google 声线列表接口刷新可选声线，并对旧数据做本地缓存。
- 核心函数可直接在脚本或其他项目中复用，便于集成自动化流程。

## 环境要求
- Python 3.10 或更新版本（需包含 Tkinter）。
- 操作系统：Windows / macOS / 大多数 Linux 发行版。
- 依赖库：`google-genai`、`requests`（GUI 额外依赖标准库 Tkinter）。
- Google Gemini API Key（可在设置对话框或配置文件中填写）。

## 快速开始
1. 创建虚拟环境并安装依赖：
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate            # Windows
   # source .venv/bin/activate           # macOS / Linux
   python -m pip install --upgrade pip
   python -m pip install google-genai requests
   ```
2. 启动桌面应用：
   ```powershell
   python igtts_gui.py
   ```
3. 首次使用时在“设置”对话框中填入 Gemini API Key，可选地调整模型、默认输出路径与日志文件。

## 配置文件说明
`config.json` 记录所有持久化设置，示例字段：
- `api_key`：Gemini API Key。
- `base_url`：可选，自定义 Gemini 服务地址。
- `model`：默认使用 `gemini-2.5-pro-preview-tts`。
- `default_voice` / `default_output`：默认为 Zephyr / `output.wav`。
- `voices`、`voices_cached_at`：声线缓存与时间戳。
- `batch_tasks_path`：上次导入/导出批量任务的路径。
- `multi_delay_seconds`：批量生成时的间隔秒数。

可在 GUI 中更新主要设置；也可手动编辑 JSON 后重新启动应用使其生效。

## 使用指南
### 单次生成
1. 在“单次模式”页签输入待合成文本，可通过“加载文本…”按钮读取本地文件。
2. 从下拉框选择声线，或在“手动声线 ID”中直接输入。
3. 指定输出 WAV 路径并点击“开始生成”。

### 批量生成
1. 在“批量模式”页签使用多行文本框填写多条记录，可点击“添加条目”扩展。
2. 若某条未指定输出文件，将按 `默认文件名_序号.wav` 自动生成。
3. 可设置每条生成之间的延迟秒数，点击“开始生成”后可随时“停止”。

### 批量任务文件
- 导入/导出按钮支持以文本文件保存批量任务，字段以 `|` 分隔，换行与分隔符通过反斜杠转义。
- 示例：`你好世界 | Zephyr | C:\\audio\\hello.wav`

### 日志与状态
- GUI 底部展示最新日志，可在设置中选择调试模式并指定日志文件路径（默认为 `log.log`）。

## 脚本示例
若需在自动化脚本中复用核心函数，可直接调用 `gemini_tts`：
```python
from igtts import gemini_tts, load_config

config = load_config()
gemini_tts("你好，世界！", voice="Zephyr", output_path="hello.wav", config=config)
```

## 编译 / 打包教程
若希望分发为独立可执行文件，可使用 PyInstaller：
1. 确保依赖已安装：
   ```powershell
   python -m pip install pyinstaller google-genai requests
   ```
2. 运行打包命令（Windows 示例）：
   ```powershell
   pyinstaller igtts_gui.py \
     --name "Gemini TTS 工具集" \
     --noconsole \
     --add-data "config.json;." \
     --add-data "input.txt;."
   ```
   - `--noconsole` 关闭控制台窗口。
   - `--add-data` 将默认配置与示例输入拷贝到可执行文件同级目录。
3. 构建完成后，生成文件位于 `dist/Gemini TTS 工具集/`。首启前可在该目录下编辑 `config.json`，或直接在应用内更新设置。
4. macOS / Linux 打包时，将 `--add-data "源:目标"` 中的分隔符改为 `:`。

## 故障排查
- **未填写 API Key**：调用 `gemini_tts` 会抛出 `ValueError: Gemini API key missing`。请在配置中填写。
- **声线列表无法刷新**：检查网络连通性与 API 权限，失败时应用会恢复本地缓存。
- **生成失败**：查看 GUI 下方或 `log.log` 获取详细错误信息，常见原因包括无效声线 ID 或网络波动。

如需更多帮助，可结合 `CHANGELOG.md` 了解最新更新内容。

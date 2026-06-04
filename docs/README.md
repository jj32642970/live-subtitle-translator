# 实时字幕翻译器

把电脑正在播放的声音识别成字幕，并翻译成中文。网页用于实时展示，`data/transcript.txt` 用于事后查看完整句翻译。

## 目录结构

```text
app.py                 后端主程序
web/                  前端字幕页面
config/               配置文件
scripts/              启动、停止、查看状态脚本
docs/                 文档
data/                 字幕输出文件
logs/                 运行日志
runtime/              运行时锁文件
```

## 快速使用

双击根目录：

```text
start.bat   启动
stop.bat    停止
status.bat  查看状态
```

字幕页面：

```text
http://127.0.0.1:8080
```

OBS 透明背景页面：

```text
http://127.0.0.1:8080?transparent=1
```

## 配置

主配置文件：

```text
config/config.toml
```

常用项：

```toml
[audio]
mode = "loopback"
segment_seconds = 2.0

[recognition]
source_language = "ru"

[translation]
model = "deepseek-v4-flash"
context_prompt = "这里填写视频/直播场景说明，帮助翻译更准确。"

[output]
transcript_path = "data/transcript.txt"
```

## 手动运行

```powershell
.\.venv\Scripts\python.exe -u app.py --config config\config.toml
```

查看设备：

```powershell
.\.venv\Scripts\python.exe app.py --list-devices
```

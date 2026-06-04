# 使用说明

## 启动和停止

推荐直接双击：

```text
start.bat   启动服务
stop.bat    停止服务
status.bat  查看服务状态和最近日志
```

脚本会避免重复启动。如果已有服务在运行，再次启动会直接提示 `Already running`。

启动后会自动打开透明悬浮字幕窗。悬浮窗可拖动，按 `Esc` 只关闭悬浮窗，不会停止后台翻译服务。

## 页面地址

实时字幕页面：

```text
http://127.0.0.1:8080
```

OBS 透明背景地址：

```text
http://127.0.0.1:8080?transparent=1
```

## 配置文件

当前配置：

```text
config/config.toml
```

示例配置：

```text
config/config.example.toml
```

关键配置：

```toml
[audio]
mode = "loopback"
sample_rate = 16000
segment_seconds = 2.0

[recognition]
source_language = "ru"
model_size = "small"
device = "cpu"
compute_type = "int8"

[translation]
target_language = "zh"
model = "deepseek-v4-flash"
context_prompt = "这里填写视频场景说明。"

[output]
transcript_path = "data/transcript.txt"

[overlay]
enabled = true
```

`context_prompt` 用于告诉翻译模型当前内容是什么场景，例如游戏直播、体育采访、会议讨论等。

## 输出文件

完整句字幕保存到：

```text
data/transcript.txt
```

运行日志保存到：

```text
logs/live.log
logs/live.err.log
```

## 手动命令

启动：

```powershell
.\.venv\Scripts\python.exe -u app.py --config config\config.toml
```

停止时可以直接用 `stop.bat`。

查看音频设备：

```powershell
.\.venv\Scripts\python.exe app.py --list-devices
```

## 常见问题

页面显示“连接中”：后端服务没有启动，运行 `start.bat`。

页面显示“已断开”：后端服务中断，运行 `status.bat` 看日志，然后重新运行 `start.bat`。

没有声音：确认视频声音输出到当前 Windows 默认播放设备，并确认 `mode = "loopback"`。

翻译不准：修改 `config/config.toml` 里的 `context_prompt`，补充视频类型、人名、术语、游戏或比赛背景。

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import os
import queue
import random
import re
import socket
import threading
import time
import tomllib
import urllib.error
import urllib.request
import warnings
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
import websockets
from faster_whisper import WhisperModel


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
LOCK_FILE = ROOT / "runtime" / "live-subtitle-translator.lock"


@dataclass
class AudioConfig:
    mode: str = "input"
    device: int | str | None = None
    sample_rate: int = 16000
    segment_seconds: float = 4.0


@dataclass
class RecognitionConfig:
    source_language: str = "auto"
    model_size: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"


@dataclass
class TranslationConfig:
    target_language: str = "zh"
    show_original: bool = True
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4.1-mini"
    context_prompt: str = ""


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    http_port: int = 8080
    ws_port: int = 8765


@dataclass
class OverlayConfig:
    enabled: bool = True
    x: int = 80
    y: int = 760
    width: int = 1200
    height: int = 180
    opacity: float = 0.92
    translation_font_size: int = 34
    original_font_size: int = 18
    show_original: bool = True


@dataclass
class OutputConfig:
    transcript_path: str = "data/transcript.txt"
    append_blank_line: bool = True
    complete_sentence_timeout_seconds: float = 8.0
    max_sentence_chars: int = 320
    min_sentence_chars: int = 8


@dataclass
class AppConfig:
    audio: AudioConfig
    recognition: RecognitionConfig
    translation: TranslationConfig
    server: ServerConfig
    overlay: OverlayConfig
    output: OutputConfig


def load_config(path: Path | None) -> AppConfig:
    raw: dict[str, Any] = {}
    if path and path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))

    return AppConfig(
        audio=AudioConfig(**raw.get("audio", {})),
        recognition=RecognitionConfig(**raw.get("recognition", {})),
        translation=TranslationConfig(**raw.get("translation", {})),
        server=ServerConfig(**raw.get("server", {})),
        overlay=OverlayConfig(**raw.get("overlay", {})),
        output=OutputConfig(**raw.get("output", {})),
    )


class SubtitleHub:
    def __init__(self) -> None:
        self.clients: set[Any] = set()
        self.local_listeners: list[Any] = []
        self.last_payload: dict[str, Any] | None = None

    async def register(self, websocket: Any) -> None:
        self.clients.add(websocket)
        if self.last_payload:
            await websocket.send(json.dumps(self.last_payload, ensure_ascii=False))

    async def unregister(self, websocket: Any) -> None:
        self.clients.discard(websocket)

    def add_local_listener(self, listener: Any) -> None:
        self.local_listeners.append(listener)

    async def publish(self, payload: dict[str, Any]) -> None:
        self.last_payload = payload
        for listener in tuple(self.local_listeners):
            try:
                listener(payload)
            except Exception as exc:
                print(f"[本地字幕监听器错误] {exc}")
        if not self.clients:
            return
        message = json.dumps(payload, ensure_ascii=False)
        await asyncio.gather(
            *(client.send(message) for client in tuple(self.clients)),
            return_exceptions=True,
        )


def list_loopback_devices() -> None:
    try:
        import soundcard as sc
    except ImportError:
        print("使用 Windows WASAPI 回环采集需要安装 soundcard：pip install soundcard")
        return

    print("\nWindows WASAPI 回环播放设备：")
    for index, speaker in enumerate(sc.all_speakers()):
        default_marker = "*" if speaker.id == sc.default_speaker().id else " "
        print(f"{default_marker} {index}: {speaker.name} ({speaker.channels} channels)")


def list_devices() -> None:
    print(sd.query_devices())
    list_loopback_devices()


def start_http_server(host: str, port: int) -> ThreadingHTTPServer:
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def start_overlay_window(hub: SubtitleHub, cfg: OverlayConfig, server_cfg: ServerConfig) -> None:
    if not cfg.enabled:
        return

    updates: queue.Queue[dict[str, Any]] = queue.Queue()
    hub.add_local_listener(lambda payload: updates.put(payload))

    def run_window() -> None:
        try:
            import tkinter as tk
        except ImportError:
            print("悬浮窗启动失败：当前 Python 没有 tkinter。")
            return

        transparent_color = "#010101"
        root = tk.Tk()
        root.title("实时字幕悬浮窗")
        root.geometry(f"{cfg.width}x{cfg.height}+{cfg.x}+{cfg.y}")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", max(0.2, min(cfg.opacity, 1.0)))
        root.configure(bg=transparent_color)
        try:
            root.attributes("-transparentcolor", transparent_color)
        except tk.TclError:
            pass

        frame = tk.Frame(root, bg=transparent_color)
        frame.pack(fill="both", expand=True)

        def close_tool() -> None:
            print("已通过悬浮窗关闭按钮退出。")
            root.destroy()
            os._exit(0)

        subtitle_panel = tk.Frame(root, bg="#000000")
        subtitle_panel.place(relx=0.5, rely=1.0, anchor="s", relwidth=0.96)

        control_row = tk.Frame(subtitle_panel, bg="#000000", height=30)
        control_row.pack(side="top", fill="x")
        control_row.pack_propagate(False)

        close_button = tk.Button(
            control_row,
            text="×",
            command=close_tool,
            fg="#ffffff",
            bg="#8b1e2d",
            activeforeground="#ffffff",
            activebackground="#b3263a",
            relief="flat",
            font=("Microsoft YaHei UI", 13, "bold"),
            width=4,
            cursor="hand2",
        )
        close_button.config(text="X")
        close_button.pack(side="right", fill="y")

        captions_frame = tk.Frame(subtitle_panel, bg="#000000")
        captions_frame.pack(side="top", fill="x")

        translated_label = tk.Label(
            captions_frame,
            text="等待字幕...",
            fg="#ffffff",
            bg="#000000",
            font=("Microsoft YaHei UI", cfg.translation_font_size, "bold"),
            wraplength=max(200, cfg.width - 80),
            justify="center",
            padx=18,
            pady=8,
        )
        translated_label.pack(side="bottom", fill="x")

        original_label = tk.Label(
            captions_frame,
            text="",
            fg="#dbe8ee",
            bg="#000000",
            font=("Microsoft YaHei UI", cfg.original_font_size),
            wraplength=max(200, cfg.width - 100),
            justify="center",
            padx=16,
            pady=6,
        )
        original_label.pack(side="bottom", fill="x")

        drag_start = {"x": 0, "y": 0}

        def start_drag(event: Any) -> None:
            drag_start["x"] = event.x
            drag_start["y"] = event.y

        def drag(event: Any) -> None:
            x = root.winfo_x() + event.x - drag_start["x"]
            y = root.winfo_y() + event.y - drag_start["y"]
            root.geometry(f"+{x}+{y}")

        def poll_updates() -> None:
            latest = None
            while True:
                try:
                    latest = updates.get_nowait()
                except queue.Empty:
                    break
            if latest:
                translated_label.config(text=latest.get("translation") or latest.get("text") or "")
                if cfg.show_original and latest.get("text"):
                    original_label.config(text=latest.get("text") or "")
                else:
                    original_label.config(text="")
            root.after(100, poll_updates)

        root.bind("<ButtonPress-1>", start_drag)
        root.bind("<B1-Motion>", drag)
        root.bind("<Escape>", lambda _event: root.destroy())
        poll_updates()
        print("悬浮字幕窗已打开。可拖动窗口，按 Esc 关闭悬浮窗；点右上角 × 退出整个工具。")
        root.mainloop()

    thread = threading.Thread(target=run_window, daemon=True)
    thread.start()


def ensure_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise RuntimeError(f"端口 {host}:{port} 已被占用，请先停止旧的字幕服务。") from exc


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        self.handle.seek(0)

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("已有一个实时字幕翻译器实例正在运行，请不要重复启动。") from exc

        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"pid={os.getpid()}\nstarted_at={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.handle.flush()

    def release(self) -> None:
        if not self.handle:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def resolve_output_path(path: str) -> Path:
    output_path = Path(path)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    return output_path


def append_transcript_pair(text: str, translation: str, cfg: OutputConfig) -> None:
    output_path = resolve_output_path(cfg.transcript_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    separator = "\n\n" if cfg.append_blank_line else "\n"
    output_path.open("a", encoding="utf-8").write(f"{text}\n{translation}{separator}")


def is_near_duplicate(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    if previous == current:
        return True
    shorter, longer = sorted((previous, current), key=len)
    return len(shorter) > 12 and shorter in longer


class SentenceBuffer:
    def __init__(self, cfg: OutputConfig) -> None:
        self.cfg = cfg
        self.buffer = ""
        self.started_at: float | None = None

    def add(self, text: str) -> list[str]:
        text = normalize_text(text)
        if not text:
            return []

        now = time.time()
        if not self.buffer:
            self.started_at = now
            self.buffer = text
        elif is_near_duplicate(self.buffer, text):
            self.buffer = text if len(text) > len(self.buffer) else self.buffer
        else:
            self.buffer = normalize_text(f"{self.buffer} {text}")

        completed = self.take_complete_sentences()
        if completed:
            return completed

        waited = now - self.started_at if self.started_at else 0
        if waited >= self.cfg.complete_sentence_timeout_seconds or len(self.buffer) >= self.cfg.max_sentence_chars:
            return self.flush()
        return []

    def take_complete_sentences(self) -> list[str]:
        completed: list[str] = []
        while True:
            match = re.match(r"^(.+?[.!?])(?:\s+|$)(.*)$", self.buffer)
            if not match:
                break
            sentence = normalize_text(match.group(1))
            remainder = normalize_text(match.group(2))
            if sentence.endswith("..."):
                break
            if len(sentence) >= self.cfg.min_sentence_chars:
                completed.append(sentence)
            self.buffer = remainder
            self.started_at = time.time() if self.buffer else None
            if not self.buffer:
                break
        return completed

    def flush(self) -> list[str]:
        sentence = normalize_text(self.buffer)
        self.buffer = ""
        self.started_at = None
        if len(sentence) < self.cfg.min_sentence_chars:
            return []
        return [sentence]

    def flush_if_ready(self) -> list[str]:
        if not self.buffer or not self.started_at:
            return []
        waited = time.time() - self.started_at
        if waited >= self.cfg.complete_sentence_timeout_seconds or len(self.buffer) >= self.cfg.max_sentence_chars:
            return self.flush()
        return []


def translate_text(text: str, cfg: TranslationConfig, source_language: str | None) -> str:
    if not cfg.api_key:
        return text

    prompt = (
        "请把转写文本翻译成简洁、自然的简体中文字幕。"
        "人名、数字、专有名词和技术术语要尽量准确。只返回译文，不要解释。"
    )
    if source_language:
        prompt += f" 源语言提示：{source_language}。"
    if cfg.target_language != "zh":
        prompt += f" 目标语言代码：{cfg.target_language}。"
    if cfg.context_prompt:
        prompt += f" 这段文本的场景说明：{cfg.context_prompt.strip()}"

    body = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0.2,
        "max_tokens": 160,
    }
    if "deepseek" in cfg.api_base.casefold() or cfg.model.startswith("deepseek-"):
        body["thinking"] = {"type": "disabled"}
    endpoint = cfg.api_base.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return normalize_text(data["choices"][0]["message"]["content"])
    except (TimeoutError, OSError, urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError) as exc:
        return f"[翻译失败] {text} ({exc})"


class AudioSegmenter:
    def __init__(self, cfg: AudioConfig) -> None:
        self.cfg = cfg
        self.samples: queue.Queue[np.ndarray] = queue.Queue()

    def callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if status:
            print(status)
        self.samples.put(indata[:, 0].copy())

    def segments(self) -> Any:
        if self.cfg.mode == "loopback":
            yield from self.loopback_segments()
            return
        if self.cfg.mode != "input":
            raise ValueError(f"Unsupported audio.mode: {self.cfg.mode}")

        frames_per_segment = int(self.cfg.sample_rate * self.cfg.segment_seconds)
        buffer = np.empty((0,), dtype=np.float32)

        with sd.InputStream(
            device=self.cfg.device,
            channels=1,
            samplerate=self.cfg.sample_rate,
            dtype="float32",
            callback=self.callback,
        ):
            while True:
                buffer = np.concatenate((buffer, self.samples.get()))
                if len(buffer) >= frames_per_segment:
                    segment = buffer[:frames_per_segment]
                    buffer = buffer[frames_per_segment:]
                    yield segment

    def loopback_segments(self) -> Any:
        try:
            import soundcard as sc
        except ImportError as exc:
            raise RuntimeError("audio.mode='loopback' 需要 soundcard。请运行：pip install soundcard") from exc

        warnings.filterwarnings("ignore", message="data discontinuity in recording")
        microphone = self.find_loopback_microphone(sc)
        frames_per_segment = int(self.cfg.sample_rate * self.cfg.segment_seconds)
        print(f"正在采集 Windows 回环音频：{microphone.name}")

        with microphone.recorder(samplerate=self.cfg.sample_rate, channels=2) as recorder:
            while True:
                data = recorder.record(numframes=frames_per_segment)
                audio = np.asarray(data, dtype=np.float32)
                if audio.ndim == 2:
                    audio = audio.mean(axis=1)
                yield audio.reshape(-1)

    def find_loopback_microphone(self, sc: Any) -> Any:
        loopbacks = [
            microphone
            for microphone in sc.all_microphones(include_loopback=True)
            if getattr(microphone, "isloopback", False)
        ]
        if not loopbacks:
            raise RuntimeError("没有找到 Windows WASAPI 回环设备。")

        if self.cfg.device is None:
            speaker = sc.default_speaker()
            for microphone in loopbacks:
                if microphone.id == speaker.id:
                    return microphone
            return loopbacks[0]

        if isinstance(self.cfg.device, int):
            if self.cfg.device < 0 or self.cfg.device >= len(loopbacks):
                raise ValueError(f"回环设备索引超出范围：{self.cfg.device}")
            return loopbacks[self.cfg.device]

        device_name = str(self.cfg.device).casefold()
        for microphone in loopbacks:
            if device_name in microphone.name.casefold() or device_name == microphone.id.casefold():
                return microphone
        raise ValueError(f"找不到回环设备：{self.cfg.device}")


def transcribe_segment(
    model: WhisperModel,
    audio: np.ndarray,
    cfg: RecognitionConfig,
) -> tuple[str, str | None]:
    language = None if cfg.source_language == "auto" else cfg.source_language
    segments, info = model.transcribe(
        audio,
        language=language,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    text = normalize_text("".join(segment.text for segment in segments))
    detected = getattr(info, "language", None)
    return text, detected


async def run_websocket_server(hub: SubtitleHub, host: str, port: int) -> None:
    async def handler(websocket: Any) -> None:
        await hub.register(websocket)
        try:
            await websocket.wait_closed()
        finally:
            await hub.unregister(websocket)

    async with websockets.serve(handler, host, port):
        await asyncio.Future()


async def run_mock(hub: SubtitleHub, cfg: AppConfig) -> None:
    examples = [
        ("The speaker is introducing the agenda for today's livestream.", "en"),
        ("この機能は自動言語認識にも対応しています。", "ja"),
        ("오늘 방송에서는 실시간 자막을 테스트합니다.", "ko"),
    ]
    while True:
        text, lang = random.choice(examples)
        translation = translate_text(text, cfg.translation, lang)
        await hub.publish(
            {
                "text": text,
                "translation": translation,
                "language": lang,
                "show_original": cfg.translation.show_original,
                "created_at": time.time(),
            }
        )
        await asyncio.sleep(3)


async def run_live(hub: SubtitleHub, cfg: AppConfig) -> None:
    print(f"正在加载 Whisper 模型：{cfg.recognition.model_size}")
    loop = asyncio.get_running_loop()
    model = await loop.run_in_executor(
        None,
        lambda: WhisperModel(
            cfg.recognition.model_size,
            device=cfg.recognition.device,
            compute_type=cfg.recognition.compute_type,
        ),
    )
    segmenter = AudioSegmenter(cfg.audio)
    segments = segmenter.segments()
    previous_text = ""
    sentence_buffer = SentenceBuffer(cfg.output)
    sentence_queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()

    async def sentence_translation_worker() -> None:
        while True:
            sentence, source_language = await sentence_queue.get()
            try:
                translation = await loop.run_in_executor(
                    None,
                    translate_text,
                    sentence,
                    cfg.translation,
                    source_language,
                )
                append_transcript_pair(sentence, translation, cfg.output)
                await hub.publish(
                    {
                        "text": sentence,
                        "translation": translation,
                        "language": source_language,
                        "show_original": cfg.translation.show_original,
                        "created_at": time.time(),
                    }
                )
                print(f"[{source_language}] {sentence} -> {translation}")
            except Exception as exc:
                print(f"[完整句翻译任务错误] {exc}")
            finally:
                sentence_queue.task_done()

    asyncio.create_task(sentence_translation_worker())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as audio_executor:
        while True:
            audio = await loop.run_in_executor(audio_executor, next, segments)
            text, detected = await loop.run_in_executor(
                None,
                transcribe_segment,
                model,
                audio,
                cfg.recognition,
            )
            source_language = detected if cfg.recognition.source_language == "auto" else cfg.recognition.source_language
            if not text or is_near_duplicate(previous_text, text):
                for sentence in sentence_buffer.flush_if_ready():
                    await sentence_queue.put((sentence, source_language))
                continue
            previous_text = text

            completed_sentences = sentence_buffer.add(text)
            if not completed_sentences:
                print(f"[缓存片段:{source_language}] {text}")
            for sentence in completed_sentences:
                await sentence_queue.put((sentence, source_language))


async def main() -> None:
    parser = argparse.ArgumentParser(description="实时多语言字幕翻译器")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "config.toml")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    cfg = load_config(args.config)
    if cfg.translation.api_key.startswith("$"):
        cfg.translation.api_key = os.environ.get(cfg.translation.api_key[1:], "")

    instance_lock = SingleInstanceLock(LOCK_FILE)
    instance_lock.acquire()
    try:
        hub = SubtitleHub()
        ensure_port_available(cfg.server.host, cfg.server.http_port)
        ensure_port_available(cfg.server.host, cfg.server.ws_port)
        start_http_server(cfg.server.host, cfg.server.http_port)
        start_overlay_window(hub, cfg.overlay, cfg.server)
        print("启动检查通过：没有发现重复运行实例，端口可用。")
        print(f"字幕页面：http://{cfg.server.host}:{cfg.server.http_port}")
        print(f"OBS 透明背景页面：http://{cfg.server.host}:{cfg.server.http_port}?transparent=1")
        if cfg.overlay.enabled:
            print("悬浮窗模式：已启用。")
        print(f"字幕保存文件：{resolve_output_path(cfg.output.transcript_path)}")

        ws_task = asyncio.create_task(run_websocket_server(hub, cfg.server.host, cfg.server.ws_port))
        worker_task = asyncio.create_task(run_mock(hub, cfg) if args.mock else run_live(hub, cfg))
        await asyncio.gather(ws_task, worker_task)
    finally:
        instance_lock.release()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("已停止")
    except RuntimeError as exc:
        print(f"启动失败：{exc}")

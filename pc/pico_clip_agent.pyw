import ctypes
import importlib.util
import json
import socket
import struct
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import pyaudiowpatch as pyaudio

from switchbot_lock_job import start_lock_check_thread


def _load_secrets():
    path = Path(__file__).resolve().parent / "secrets.py"
    if not path.exists():
        raise ImportError(
            "pc/secrets.py がありません。secrets.example.py をコピーして設定してください"
        )
    spec = importlib.util.spec_from_file_location("pico_bridge_pc_secrets", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_secrets = _load_secrets()
PICO_URL = _secrets.PICO_URL.rstrip("/")
AUDIO_PORT = int(getattr(_secrets, "AUDIO_PORT", 8081))

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.OpenClipboard.argtypes = [ctypes.c_void_p]
user32.OpenClipboard.restype = ctypes.c_bool
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = ctypes.c_bool
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = ctypes.c_bool
user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p

kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = ctypes.c_bool


def audio_server_thread():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", AUDIO_PORT))
    srv.listen(5)

    while True:
        try:
            cl, _ = srv.accept()
            threading.Thread(target=handle_audio_client, args=(cl,), daemon=True).start()
        except Exception:
            time.sleep(0.5)


def handle_audio_client(cl):
    try:
        cl.settimeout(2.0)
        req = cl.recv(1024).decode("utf-8", "ignore")
        first_line = req.split("\r\n", 1)[0]
        path = first_line.split(" ")[1] if " " in first_line else "/"

        if path.startswith("/stream"):
            p = pyaudio.PyAudio()
            try:
                wasapi_info = p.get_default_wasapi_loopback()
                rate = int(wasapi_info["defaultSampleRate"])
                ch = min(2, int(wasapi_info["maxInputChannels"]))
                chunk_frames = 1024
                silence_chunk = b"\x00" * (chunk_frames * ch * 2)
                last_pcm = [time.monotonic()]
                alive = [True]

                headers = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/octet-stream\r\n"
                    "Access-Control-Allow-Origin: *\r\n"
                    "Access-Control-Expose-Headers: X-Sample-Rate, X-Channels\r\n"
                    f"X-Sample-Rate: {rate}\r\n"
                    f"X-Channels: {ch}\r\n"
                    "Cache-Control: no-store\r\n"
                    "Connection: close\r\n\r\n"
                )
                cl.sendall(headers.encode("utf-8"))
                cl.settimeout(2.0)

                def on_audio(in_data, frame_count, time_info, status):
                    last_pcm[0] = time.monotonic()
                    try:
                        cl.sendall(in_data)
                        return (None, pyaudio.paContinue)
                    except Exception:
                        alive[0] = False
                        return (None, pyaudio.paComplete)

                stream = p.open(
                    format=pyaudio.paInt16,
                    channels=ch,
                    rate=rate,
                    frames_per_buffer=chunk_frames,
                    input=True,
                    input_device_index=wasapi_info["index"],
                    stream_callback=on_audio,
                )
                stream.start_stream()
                try:
                    # PCが無音の間もストリームが途切れないよう無音パケットで補完
                    while alive[0] and stream.is_active():
                        time.sleep(0.02)
                        if time.monotonic() - last_pcm[0] > 0.04:
                            try:
                                cl.sendall(silence_chunk)
                                last_pcm[0] = time.monotonic()
                            except Exception:
                                break
                finally:
                    stream.stop_stream()
                    stream.close()
            finally:
                p.terminate()
    except Exception:
        pass
    finally:
        try:
            cl.close()
        except Exception:
            pass


def get_win_clipboard():
    for _ in range(5):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    except Exception:
        return ""
    finally:
        user32.CloseClipboard()


def set_win_clipboard(text):
    for _ in range(5):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return False
    try:
        user32.EmptyClipboard()
        raw = (text + "\0").encode("utf-16-le")
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
        if not h_mem:
            return False
        ptr = kernel32.GlobalLock(h_mem)
        if not ptr:
            return False
        ctypes.memmove(ptr, raw, len(raw))
        kernel32.GlobalUnlock(h_mem)
        user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        return True
    finally:
        user32.CloseClipboard()


def post_json(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        PICO_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(path):
    with urllib.request.urlopen(PICO_URL + path, timeout=2.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    # AudioRelay代替の低遅延WASAPI音声ストリーミングサーバーをポート8081で起動
    threading.Thread(target=audio_server_thread, daemon=True).start()
    # SwitchBot 鍵監視（secrets に TOKEN 等があるときだけ起動）
    start_lock_check_thread(_secrets)

    last_pc_text = ""
    last_seq = -1

    while True:
        try:
            state = get_json("/clip?pc=1")
            seq = state.get("seq", 0)
            src = state.get("src", "init")
            server_text = state.get("text", "")

            if seq < last_seq or (seq == 0 and src == "init"):
                last_pc_text = ""

            if seq != last_seq:
                last_seq = seq
                if src == "phone":
                    phone_text = server_text
                    action = state.get("action", "copy")
                    if phone_text:
                        last_pc_text = phone_text
                        set_win_clipboard(phone_text)
                    if action == "paste":
                        time.sleep(0.08)
                        get_json("/k?m=paste")
                    elif action == "open":
                        target = phone_text.strip()
                        if target:
                            if not target.startswith(("http://", "https://")):
                                target = "https://" + target
                            webbrowser.open(target)

            cur_pc = get_win_clipboard()
            if cur_pc and len(cur_pc) <= 2048:
                if cur_pc != last_pc_text or (src == "init" and not server_text):
                    last_pc_text = cur_pc
                    res = post_json("/clip", {"text": cur_pc, "src": "pc", "action": "copy"})
                    last_seq = res.get("seq", last_seq)
        except Exception:
            pass

        time.sleep(0.6)


if __name__ == "__main__":
    main()

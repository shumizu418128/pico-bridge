import gc
import struct
import time
import json
import network
import socket
import select
import rp2
from machine import Pin, ADC
import usb.device
from usb.device.hid import HIDInterface
from usb.device.keyboard import KeyboardInterface, KeyCode

try:
    import secrets
except ImportError:
    raise ImportError(
        "secrets.py がありません。secrets.example.py をコピーして設定してください"
    )

WIFI_SSID = secrets.WIFI_SSID
WIFI_PASS = secrets.WIFI_PASS
AP_SSID = secrets.AP_SSID
AP_PASS = secrets.AP_PASS
PC_IP = secrets.PC_IP
PC_IP_CANDIDATES = list(getattr(secrets, "PC_IP_CANDIDATES", [PC_IP]))

_CONSUMER_DESC = (
    b"\x05\x0c\x09\x01\xa1\x01\x15\x00\x26\xff\x03"
    b"\x19\x00\x2a\xff\x03\x75\x10\x95\x01\x81\x00\xc0"
)


class MediaInterface(HIDInterface):
    def __init__(self):
        super().__init__(
            _CONSUMER_DESC,
            protocol=0,
            interface_str="Pico StreamDeck Media",
        )
        self._buf = bytearray(2)

    def tap_media(self, usage_id):
        if not self.is_open():
            return
        struct.pack_into("<H", self._buf, 0, usage_id)
        while self.busy():
            time.sleep_ms(1)
        self.send_report(self._buf)
        time.sleep_ms(30)
        struct.pack_into("<H", self._buf, 0, 0)
        while self.busy():
            time.sleep_ms(1)
        self.send_report(self._buf)


# LEDは完全消灯
led = Pin("LED", Pin.OUT)
led.off()
temp_adc = ADC(4)

kbd = KeyboardInterface()
media = MediaInterface()
usb.device.get().init(kbd, media, builtin_driver=True)


def read_temp_c():
    v = temp_adc.read_u16() * (3.3 / 65535)
    return round(27.0 - (v - 0.706) / 0.001721, 1)


def tap_keys(keys):
    if not kbd.is_open():
        return
    while kbd.busy():
        time.sleep_ms(1)
    kbd.send_keys(keys)
    time.sleep_ms(28)
    while kbd.busy():
        time.sleep_ms(1)
    kbd.send_keys([])


MEDIA_KEYS = {
    "vol_up": 0x00E9,
    "vol_down": 0x00EA,
    "vol_mute": 0x00E2,
    "play_pause": 0x00CD,
}

MACROS = {
    "cmd_pal": [KeyCode.LEFT_ALT, KeyCode.SPACE],
    "adv_paste": [KeyCode.LEFT_UI, KeyCode.LEFT_SHIFT, KeyCode.V],
    "color_pick": [KeyCode.LEFT_UI, KeyCode.LEFT_SHIFT, KeyCode.C],
    "power_ocr": [KeyCode.LEFT_UI, KeyCode.LEFT_SHIFT, KeyCode.T],
    "pin_top": [KeyCode.LEFT_UI, KeyCode.LEFT_CTRL, KeyCode.T],
    "shot": [KeyCode.LEFT_UI, KeyCode.LEFT_SHIFT, KeyCode.S],
    "dc_mute": [KeyCode.LEFT_CTRL, KeyCode.LEFT_SHIFT, KeyCode.M],
    "dc_deafen": [KeyCode.LEFT_CTRL, KeyCode.LEFT_SHIFT, KeyCode.D],
    "nv_clip": [KeyCode.LEFT_ALT, KeyCode.F10],
    "task_mgr": [KeyCode.LEFT_CTRL, KeyCode.LEFT_SHIFT, KeyCode.ESCAPE],
    "win_l": [KeyCode.LEFT_UI, KeyCode.L],
    "paste": [KeyCode.LEFT_CTRL, KeyCode.V],
}

# クリップボード共有ステート
clip_state = {
    "text": "",
    "seq": 0,
    "src": "init",       # "pc" or "phone"
    "action": "copy",    # "copy", "paste", "open"
    "pc_ip": PC_IP,
}

HTML_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>Pico Bridge & Deck</title>
<style>
*{box-sizing:border-box;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;margin:0;padding:0}
html,body{width:100%;height:100dvh;overflow:hidden;position:fixed;inset:0;background:#090d16;color:#f8fafc;font-family:system-ui,-apple-system,sans-serif;touch-action:none}
.app{display:grid;grid-template-rows:40px 168px 1fr;gap:8px;width:100%;height:100dvh;padding:8px}
.bar{display:flex;justify-content:space-between;align-items:center;padding:0 16px;background:#111827;border-radius:8px;border:1px solid #1f2937;height:40px}
.brand{font-size:15px;font-weight:800;letter-spacing:0.5px;display:flex;align-items:center;gap:8px;color:#e2e8f0}
.dot{width:8px;height:8px;border-radius:8px;background:#22c55e;box-shadow:0 0 8px #22c55e}
.meta{display:flex;gap:8px;align-items:center;font-size:12px;color:#94a3b8}
.pill{padding:0 8px;height:24px;display:flex;align-items:center;background:#1e293b;border-radius:8px;font-weight:600}
.rbtn{padding:0 8px;height:24px;display:flex;align-items:center;gap:4px;background:#0284c7;border:1px solid #38bdf8;color:#f8fafc;border-radius:8px;font-size:12px;font-weight:700;cursor:pointer}
.rbtn:active{transform:scale(0.96);background:#38bdf8;color:#090d16}

/* 双方向クリップボード＆URLブリッジ領域 (高さ168px = 8の倍数) */
.bridge{background:#111827;border:2px solid #38bdf8;border-radius:16px;padding:8px;display:grid;grid-template-rows:1fr 40px;gap:8px;height:168px}
.clip-box{display:grid;grid-template-columns:1fr 64px;gap:8px;height:100%;min-height:0}
.clip-input{width:100%;height:100%;background:#090d16;border:1px solid #334155;border-radius:8px;color:#f8fafc;padding:8px;font-size:14px;line-height:20px;resize:none;outline:none;user-select:text;-webkit-user-select:text;touch-action:manipulation}
.clip-input:focus{border-color:#38bdf8}
.clip-side{display:grid;grid-template-rows:1fr 1fr;gap:8px}
.sbtn{background:#1e293b;border:1px solid #475569;color:#e2e8f0;border-radius:8px;font-size:11px;font-weight:700;display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer}
.sbtn:active{background:#38bdf8;color:#090d16}
.bridge-btns{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;height:40px}
.bbtn{background:#1e293b;border:1px solid #38bdf8;color:#f8fafc;border-radius:8px;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;gap:4px;cursor:pointer}
.bbtn.primary{background:#0284c7;border-color:#38bdf8}
.bbtn:active{transform:scale(0.96);background:#38bdf8;color:#090d16}

/* Stream Deck キー領域 (3列×4行 = 12キー) */
.deck{display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:repeat(4,1fr);gap:8px;width:100%;height:100%;min-height:0}
.key{background:linear-gradient(160deg,#1e293b 0%,#0f172a 100%);border:2px solid #334155;border-radius:16px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;padding:8px;cursor:pointer;box-shadow:inset 0 2px 4px rgba(255,255,255,0.06);min-height:0;overflow:hidden}
.key:active,.key.flash{transform:scale(0.94);background:#1e3a8a;border-color:#38bdf8}
.key.audio-on{background:linear-gradient(160deg,#166534 0%,#064e3b 100%);border-color:#4ade80}
.ico{font-size:22px;line-height:24px}
.lbl{font-size:12px;font-weight:700;color:#f1f5f9;text-align:center;line-height:16px;white-space:nowrap}
.sub{font-size:10px;font-weight:600;color:#64748b;line-height:12px;white-space:nowrap}
.c-pt{border-color:#0ea5e9}
.c-dc{border-color:#6366f1}
.c-nv{border-color:#22c55e}
.c-md{border-color:#f59e0b}
.c-sys{border-color:#ec4899}
</style>
</head>
<body>
<div class="app">
  <div class="bar">
    <div class="brand"><span class="dot"></span>PICO BRIDGE & DECK</div>
    <div class="meta">
      <span class="pill" id="last">SYNC OK</span>
      <button class="rbtn" onclick="forceReloadClip()">🔄 再読込</button>
    </div>
  </div>

  <!-- 双方向クリップボード＆URLブリッジ -->
  <div class="bridge">
    <div class="clip-box">
      <textarea class="clip-input" id="clipText" placeholder="PCでコピーした文字/URLがここに自動表示されます。スマホから入力・音声入力してPCへ送ることもできます。"></textarea>
      <div class="clip-side">
        <button class="sbtn" onclick="copyOnPhone()">📱コピー</button>
        <button class="sbtn" onclick="clearClip()">🗑️消去</button>
      </div>
    </div>
    <div class="bridge-btns">
      <button class="bbtn" onclick="pushClip('copy')">💻 PCへコピー</button>
      <button class="bbtn primary" onclick="pushClip('paste')">⚡ PCに直接貼付</button>
      <button class="bbtn" onclick="openUrlAction()">🌐 URLを開く</button>
    </div>
  </div>

  <!-- Stream Deck 12キー -->
  <div class="deck">
    <button class="key c-pt" onclick="send('k','shot',this)">
      <span class="ico">✂️</span><span class="lbl">スクショ</span><span class="sub">Win+Shift+S</span>
    </button>
    <button class="key c-pt" onclick="send('k','power_ocr',this)">
      <span class="ico">🔍</span><span class="lbl">画面OCR</span><span class="sub">文字抽出</span>
    </button>
    <button class="key c-pt" onclick="send('k','color_pick',this)">
      <span class="ico">🎨</span><span class="lbl">スポイト</span><span class="sub">ColorPicker</span>
    </button>

    <button class="key c-pt" onclick="send('k','adv_paste',this)">
      <span class="ico">📋</span><span class="lbl">履歴ペースト</span><span class="sub">Adv Paste</span>
    </button>
    <button class="key c-pt" onclick="send('k','pin_top',this)">
      <span class="ico">📌</span><span class="lbl">最前面固定</span><span class="sub">AlwaysOnTop</span>
    </button>
    <button class="key c-pt" onclick="send('k','cmd_pal',this)">
      <span class="ico">🚀</span><span class="lbl">ランチャー</span><span class="sub">Alt+Space</span>
    </button>

    <button class="key c-dc" onclick="send('k','dc_mute',this)">
      <span class="ico">🎙️</span><span class="lbl">VCミュート</span><span class="sub">Discord</span>
    </button>
    <button class="key c-dc" id="audioBtn" onclick="togglePcAudio()">
      <span class="ico" id="audioIco">🎧</span><span class="lbl" id="audioLbl">PC音中継</span><span class="sub" id="audioSub">AudioRelay</span>
    </button>
    <button class="key c-nv" onclick="send('k','nv_clip',this)">
      <span class="ico">🎬</span><span class="lbl">リプレイ保存</span><span class="sub">Alt+F10</span>
    </button>

    <button class="key c-md" onclick="send('media','vol_down',this)">
      <span class="ico">🔉</span><span class="lbl">音量 －</span><span class="sub">Vol Down</span>
    </button>
    <button class="key c-md" onclick="send('media','play_pause',this)">
      <span class="ico">⏯️</span><span class="lbl">再生 / 停止</span><span class="sub">Media Play</span>
    </button>
    <button class="key c-md" onclick="send('media','vol_up',this)">
      <span class="ico">🔊</span><span class="lbl">音量 ＋</span><span class="sub">Vol Up</span>
    </button>
  </div>
</div>

<script>
const lastEl = document.getElementById('last');
const clipEl = document.getElementById('clipText');
let localSeq = -1;
let lastKnownText = '';
let syncing = false;
let pcIp = '__PC_IP__';

document.addEventListener('touchmove', (e) => {
  if (e.target !== clipEl) e.preventDefault();
}, {passive: false});

// 権限がある場合：スマホのクリップボードを静かに読み取る（エラーは一切出さない）
const tryReadPhoneClip = async () => {
  try {
    if (navigator.clipboard && navigator.clipboard.readText) {
      const t = await navigator.clipboard.readText();
      return t || '';
    }
  } catch (e) {}
  return null;
};

// 権限がある場合：スマホのクリップボードへ静かに書き込む（失敗時は既存機能の選択コピーへフォールバック）
const tryWritePhoneClip = async (text, manual = false) => {
  if (!text) return false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      lastKnownText = text;
      return true;
    }
  } catch (e) {}
  if (manual) {
    try {
      clipEl.select();
      document.execCommand('copy');
      lastKnownText = text;
      return true;
    } catch (e) {}
  }
  return false;
};

const send = async (type, name, btn) => {
  if (navigator.vibrate) navigator.vibrate(18);
  if (btn) {
    btn.classList.add('flash');
    setTimeout(() => btn.classList.remove('flash'), 120);
    const lbl = btn.querySelector('.lbl');
    if (lbl) lastEl.textContent = lbl.textContent;
  }
  try {
    await fetch(`/${type}?m=${name}`, {cache: 'no-store'});
  } catch (e) {}
};

const pushClip = async (action, textOverride = null, silent = false) => {
  if (!silent && navigator.vibrate) navigator.vibrate(25);
  const text = textOverride !== null ? textOverride : clipEl.value;
  if (!text) return;
  lastKnownText = text;
  if (!silent) {
    lastEl.textContent = action === 'paste' ? 'PCへ貼付中' : (action === 'open' ? 'PCでURL起動' : 'PCへ送信済');
  }
  try {
    const r = await fetch('/clip', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text, src: 'phone', action})
    });
    const d = await r.json();
    if (d.seq !== undefined) localSeq = d.seq;
  } catch (e) {}
};

const copyOnPhone = async () => {
  if (navigator.vibrate) navigator.vibrate(15);
  const t = clipEl.value;
  if (!t) return;
  const ok = await tryWritePhoneClip(t, true);
  if (ok) lastEl.textContent = 'スマホにコピー済';
};

const clearClip = () => {
  clipEl.value = '';
  clipEl.focus();
};

const openUrlAction = () => {
  const t = clipEl.value.trim();
  if (!t) return;
  pushClip('open');
};

// Stream Deck「🎧 PC音中継」キー：PCのWASAPIループバック音声をスマホでリアルタイム再生
let audioCtx = null;
let audioPlaying = false;
let audioAbort = null;
let wakeLockSentinel = null;
let wakeTimer = null;
let wakeVid = null;

// 音声再生中のみスマホの画面スリープを防止（WakeLock API ＋ VideoWakeLock フォールバック）
const startWakeLock = async () => {
  try {
    if (navigator.wakeLock && navigator.wakeLock.request) {
      wakeLockSentinel = await navigator.wakeLock.request('screen');
    }
  } catch (e) {}
  try {
    if (!wakeVid) {
      const cv = document.createElement('canvas');
      cv.width = 8;
      cv.height = 8;
      const c2d = cv.getContext('2d');
      let tick = 0;
      wakeTimer = setInterval(() => {
        if (!audioPlaying) return;
        tick ^= 1;
        c2d.fillStyle = tick ? '#090d16' : '#0a0e18';
        c2d.fillRect(0, 0, 8, 8);
      }, 500);
      wakeVid = document.createElement('video');
      wakeVid.setAttribute('playsinline', '');
      wakeVid.setAttribute('muted', '');
      wakeVid.muted = true;
      wakeVid.style.cssText = 'position:fixed;right:0;bottom:0;width:8px;height:8px;opacity:0.02;pointer-events:none;z-index:-1';
      document.body.appendChild(wakeVid);
      if (cv.captureStream) {
        wakeVid.srcObject = cv.captureStream(2);
      }
    }
    await wakeVid.play();
  } catch (e) {}
};

const stopWakeLock = async () => {
  try {
    if (wakeLockSentinel) {
      await wakeLockSentinel.release();
      wakeLockSentinel = null;
    }
  } catch (e) {}
  try {
    if (wakeVid) {
      wakeVid.pause();
    }
  } catch (e) {}
};

const setAudioUi = (on, subText) => {
  const btn = document.getElementById('audioBtn');
  const ico = document.getElementById('audioIco');
  const lbl = document.getElementById('audioLbl');
  const sub = document.getElementById('audioSub');
  btn.classList.toggle('audio-on', on);
  ico.textContent = on ? '🔊' : '🎧';
  lbl.textContent = on ? 'PC音 再生中' : 'PC音中継';
  sub.textContent = subText || (on ? 'スリープ防止中' : 'AudioRelay');
};

const togglePcAudio = async () => {
  if (navigator.vibrate) navigator.vibrate(20);
  if (audioPlaying) {
    audioPlaying = false;
    await stopWakeLock();
    if (audioAbort) audioAbort.abort();
    if (audioCtx) { try { await audioCtx.close(); } catch (e) {} audioCtx = null; }
    setAudioUi(false, 'AudioRelay');
    lastEl.textContent = 'PC音 停止';
    return;
  }
  try {
    audioPlaying = true;
    setAudioUi(true, '接続中...');
    lastEl.textContent = 'PC音 接続中';

    // タップ操作の直後にAudioContextを作成・アンロック（Android Chromeの自動再生ブロック回避）
    const AC = window.AudioContext || window.webkitAudioContext;
    audioCtx = new AC({latencyHint: 'interactive'});
    await audioCtx.resume();
    startWakeLock();

    audioAbort = new AbortController();
    let res = null;
    const candidates = [pcIp, __PC_IP_CANDIDATES__].filter((v, i, a) => v && a.indexOf(v) === i);
    for (const host of candidates) {
      const timer = setTimeout(() => { try { audioAbort.abort(); } catch (e) {} }, 1800);
      try {
        audioAbort = new AbortController();
        res = await fetch(`http://${host}:8081/stream`, {signal: audioAbort.signal, cache: 'no-store'});
        clearTimeout(timer);
        if (res && res.ok) { pcIp = host; break; }
      } catch (e) {
        clearTimeout(timer);
      }
    }
    if (!res) throw new Error('no stream');
    const rate = parseInt(res.headers.get('X-Sample-Rate') || '48000', 10);
    const ch = parseInt(res.headers.get('X-Channels') || '2', 10);

    await audioCtx.resume();
    setAudioUi(true, 'スリープ防止中');
    lastEl.textContent = 'PC音 出力中';

    const reader = res.body.getReader();
    let nextTime = audioCtx.currentTime + 0.05;
    let leftover = new Uint8Array(0);

    while (audioPlaying) {
      const {done, value} = await reader.read();
      if (done) break;
      const merged = new Uint8Array(leftover.length + value.length);
      merged.set(leftover, 0);
      merged.set(value, leftover.length);

      const frameBytes = ch * 2;
      const usable = Math.floor(merged.length / frameBytes) * frameBytes;
      leftover = merged.slice(usable);
      if (usable === 0) continue;

      const int16 = new Int16Array(merged.buffer, merged.byteOffset, usable / 2);
      const frames = int16.length / ch;
      const buf = audioCtx.createBuffer(ch, frames, rate);
      for (let c = 0; c < ch; c++) {
        const cd = buf.getChannelData(c);
        for (let i = 0; i < frames; i++) cd[i] = int16[i * ch + c] / 32768.0;
      }
      const src = audioCtx.createBufferSource();
      src.buffer = buf;
      src.connect(audioCtx.destination);
      const now = audioCtx.currentTime;
      if (nextTime < now + 0.01 || nextTime > now + 0.25) nextTime = now + 0.05;
      src.start(nextTime);
      nextTime += buf.duration;
    }
  } catch (e) {
    if (audioPlaying) {
      audioPlaying = false;
      await stopWakeLock();
      setAudioUi(false, 'AudioRelay');
      lastEl.textContent = 'PC音 切断';
    }
  }
};

// 上部「🔄 再読込」ボタン：スマホ/PCの最新クリップボードを強制再同期
const forceReloadClip = async () => {
  if (navigator.vibrate) navigator.vibrate(20);
  lastEl.textContent = '再読込中...';
  clipEl.blur();
  try {
    const r = await fetch('/clip', {cache: 'no-store'});
    const d = await r.json();
    if (d.pc_ip) pcIp = d.pc_ip;
    const serverText = d.text || '';
    const phoneClip = await tryReadPhoneClip();

    // スマホ側で新しくコピーした文字があればPCへ送り、なければPC側の文字をスマホへ強制反映＆コピー
    if (phoneClip && phoneClip !== lastKnownText && phoneClip !== serverText) {
      clipEl.value = phoneClip;
      await pushClip('copy', phoneClip, true);
      lastEl.textContent = 'スマホ➔PC更新済';
    } else {
      localSeq = d.seq !== undefined ? d.seq : localSeq;
      clipEl.value = serverText;
      if (serverText) {
        const wrote = await tryWritePhoneClip(serverText, true);
        lastEl.textContent = wrote ? 'PC➔スマホ更新済' : '再読込完了';
      } else {
        lastEl.textContent = '再読込完了';
      }
    }
  } catch (e) {
    lastEl.textContent = '通信エラー';
  }
};

// 双方向ダイレクト自動同期（開いた瞬間・戻ってきた瞬間・定期ポーリング）
const syncBothWays = async (checkPhoneFirst = false) => {
  if (syncing) return;
  syncing = true;
  try {
    const r = await fetch('/clip', {cache: 'no-store'});
    const d = await r.json();
    if (d.pc_ip) pcIp = d.pc_ip;
    const serverText = d.text || '';
    const serverChanged = (d.seq !== undefined && d.seq !== localSeq);

    // 1. PC側で新しくコピーされたものがあればスマホへ自動書き込み
    if (serverChanged) {
      localSeq = d.seq;
      if (document.activeElement !== clipEl || clipEl.value === '') {
        clipEl.value = serverText;
      }
      if (serverText && d.src === 'pc' && serverText !== lastKnownText) {
        const wrote = await tryWritePhoneClip(serverText, false);
        lastKnownText = serverText;
        lastEl.textContent = wrote ? 'PC➔スマホ同期済' : 'PCから受信';
        syncing = false;
        return;
      }
      lastKnownText = serverText;
    }

    // 2. スマホ側で新しくコピーされたものがあればPCへ自動送信（権限がある場合のみ静かに動作）
    if (checkPhoneFirst || document.hasFocus()) {
      const phoneClip = await tryReadPhoneClip();
      if (phoneClip && phoneClip !== lastKnownText && phoneClip !== serverText) {
        clipEl.value = phoneClip;
        await pushClip('copy', phoneClip, true);
        lastEl.textContent = 'スマホ➔PC同期済';
      }
    }
  } catch (e) {}
  syncing = false;
};

// 画面表示・フォーカス復帰・タップ時に即座にダイレクト同期を試行
window.addEventListener('focus', () => syncBothWays(true));
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    syncBothWays(true);
    if (audioPlaying) startWakeLock();
  }
});
document.addEventListener('pointerdown', async () => {
  // 画面タップの瞬間はユーザー操作扱いになるため、クリップボード権限が通りやすい
  if (clipEl.value && clipEl.value !== lastKnownText) {
    await tryWritePhoneClip(clipEl.value, false);
  } else {
    syncBothWays(true);
  }
}, {passive: true});

// テキスト欄に直接ペーストされた場合も自動でPCへ同期
clipEl.addEventListener('paste', () => {
  setTimeout(() => {
    if (clipEl.value && clipEl.value !== lastKnownText) {
      pushClip('copy', clipEl.value, true);
      lastEl.textContent = 'スマホ➔PC同期済';
    }
  }, 50);
});

syncBothWays(true);
setInterval(() => syncBothWays(false), 1200);
</script>
</body>
</html>
"""


def build_html():
    candidates_js = ",".join("'%s'" % ip for ip in PC_IP_CANDIDATES)
    return (
        HTML_PAGE.replace("__PC_IP__", PC_IP).replace(
            "__PC_IP_CANDIDATES__", candidates_js
        )
    )


def setup_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(25):
        if wlan.isconnected():
            break
        time.sleep_ms(300)

    ap = network.WLAN(network.AP_IF)
    ap.config(essid=AP_SSID, password=AP_PASS)
    ap.active(True)

    sta_ip = wlan.ifconfig()[0] if wlan.isconnected() else None
    ap_ip = ap.ifconfig()[0]
    led.off()
    return sta_ip, ap_ip


def parse_query(qs):
    res = {}
    for part in qs.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            res[k] = v
    return res


def recv_full_http(cl):
    cl.settimeout(0.8)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = cl.recv(1024)
        if not chunk:
            break
        data += chunk
    if b"\r\n\r\n" not in data:
        return data.decode("utf-8", "ignore"), ""
    head_bytes, body_bytes = data.split(b"\r\n\r\n", 1)
    head_str = head_bytes.decode("utf-8", "ignore")
    content_len = 0
    for line in head_str.split("\r\n"):
        if line.lower().startswith("content-length:"):
            try:
                content_len = int(line.split(":", 1)[1].strip())
            except Exception:
                content_len = 0
    while len(body_bytes) < content_len:
        chunk = cl.recv(1024)
        if not chunk:
            break
        body_bytes += chunk
    return head_str, body_bytes.decode("utf-8", "ignore")


def run():
    sta_ip, ap_ip = setup_wifi()
    print("STATUS_IPS:", sta_ip, ap_ip)
    html_page = build_html()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 80))
    srv.listen(5)
    srv.setblocking(False)

    poller = select.poll()
    poller.register(srv, select.POLLIN)

    last_bootsel = 0

    while True:
        b = rp2.bootsel_button()
        if b and not last_bootsel:
            media.tap_media(MEDIA_KEYS["play_pause"])
            time.sleep_ms(50)
        last_bootsel = b

        events = poller.poll(20)
        for sock, _ in events:
            if sock is srv:
                try:
                    cl, addr = srv.accept()
                    head_str, body_str = recv_full_http(cl)
                    line = head_str.split("\r\n", 1)[0]
                    parts = line.split(" ")
                    method = parts[0] if len(parts) > 0 else "GET"
                    path_full = parts[1] if len(parts) > 1 else "/"
                    if "?" in path_full:
                        path, qs = path_full.split("?", 1)
                        q = parse_query(qs)
                    else:
                        path, q = path_full, {}

                    if path == "/clip":
                        if q.get("pc") == "1" and addr and addr[0]:
                            clip_state["pc_ip"] = addr[0]
                        if method == "POST":
                            try:
                                payload = json.loads(body_str)
                                new_text = payload.get("text", "")
                                src = payload.get("src", "phone")
                                action = payload.get("action", "copy")
                                if src == "pc" and addr and addr[0]:
                                    clip_state["pc_ip"] = addr[0]
                                if new_text != clip_state["text"] or src == "phone":
                                    clip_state["text"] = new_text[:2048]
                                    clip_state["seq"] += 1
                                    clip_state["src"] = src
                                    clip_state["action"] = action
                            except Exception:
                                pass
                        resp = json.dumps({
                            "text": clip_state["text"],
                            "seq": clip_state["seq"],
                            "src": clip_state["src"],
                            "action": clip_state["action"],
                            "pc_ip": clip_state["pc_ip"],
                            "temp": read_temp_c(),
                        })
                        cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nConnection: close\r\n\r\n" + resp.encode("utf-8"))
                    elif path == "/k":
                        m_name = q.get("m", "")
                        if m_name in MACROS:
                            tap_keys(MACROS[m_name])
                        body = '{"ok":1,"temp":' + str(read_temp_c()) + "}"
                        cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n" + body.encode())
                    elif path == "/media":
                        m_name = q.get("m", "")
                        if m_name in MEDIA_KEYS:
                            media.tap_media(MEDIA_KEYS[m_name])
                        body = '{"ok":1,"temp":' + str(read_temp_c()) + "}"
                        cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n" + body.encode())
                    elif path == "/stat":
                        body = '{"temp":' + str(read_temp_c()) + "}"
                        cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n" + body.encode())
                    else:
                        cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n")
                        cl.sendall(html_page.encode("utf-8"))
                except Exception:
                    pass
                finally:
                    try:
                        cl.close()
                    except Exception:
                        pass
                    gc.collect()


run()

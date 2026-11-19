# pico-bridge

Raspberry Pi Pico W を使った **双方向クリップボード / URL ブリッジ + Stream Deck**。

- スマホブラウザ: `http://<PicoのIP>/`
- PC エージェント: クリップボード同期 + WASAPI 音声中継 (`:8081`) + SwitchBot 鍵監視（5分ごと → 解錠なら Slack）

## 構成

```
firmware/   Pico W 用 (MicroPython)
pc/         Windows 側エージェント
```

## セットアップ

### 1. 秘密情報

```bash
cp firmware/secrets.example.py firmware/secrets.py
cp pc/secrets.example.py pc/secrets.py
```

`secrets.py` に Wi-Fi / AP / PC IP / Pico URL を書く（**リポジトリにはコミットしない**）。

鍵監視を使う場合は `pc/secrets.py` に SwitchBot / Slack も書く（`SWITCHBOT_TOKEN`, `SWITCHBOT_CLIENT_SECRET`, `SLACK_WEBHOOK_URL`）。未設定なら鍵監視だけスキップする。

### 2. Pico へ転送

```bash
mpremote connect COMx cp firmware/secrets.py :secrets.py
mpremote connect COMx cp firmware/main.py :main.py
mpremote connect COMx reset
```

### 3. PC エージェント

```bash
uv pip install --system -r pc/requirements.txt
pythonw pc/pico_clip_agent.pyw
```

Windows スタートアップに登録する場合は、上記 `pythonw` のフルパスと `pc/pico_clip_agent.pyw` を指定する。

## ライセンス

個人利用前提の雑多スクリプト。必要なら各自で扱ってください。

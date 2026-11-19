# secrets.example.py を secrets.py にコピーして値を埋める

PICO_URL = "http://192.168.1.20"
AUDIO_PORT = 8081

# --- SwitchBot 鍵監視（未設定ならスキップ）---
# switchbot リポの .env から TOKEN / CLIENT_SECRET / SLACK_WEBHOOK_URL を転記
SWITCHBOT_TOKEN = ""
SWITCHBOT_CLIENT_SECRET = ""
SLACK_WEBHOOK_URL = ""
SWITCHBOT_LOCK_DEVICE_ID = ""
SLACK_MENTION_USER_ID = ""
# SWITCHBOT_API_BASE_URL = "https://api.switch-bot.com"
# LOCK_CHECK_INTERVAL_SECONDS = 300

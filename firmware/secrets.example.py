# secrets.example.py を secrets.py にコピーして値を埋める
# Pico へは main.py と一緒に secrets.py を転送する

WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASS = "YOUR_WIFI_PASSWORD"

# Pico 自身が出すフォールバック AP
AP_SSID = "PicoPad-Remote"
AP_PASS = "changeme123"

# PC 側エージェントの LAN IP（音声中継・候補解決用）
PC_IP = "192.168.1.10"
PC_IP_CANDIDATES = [
    "192.168.1.10",
]

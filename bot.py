import os
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

BASE = "https://api.india.delta.exchange"
SYMBOL = "BTCUSD"
RES = "5m"
MINUTES = 5
LENGTH = 500
STATE_FILE = "last_alert.txt"
IST = timezone(timedelta(hours=5, minutes=30))

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

def notify(text):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=15,
    )

def get_candles():
    import time
    end = int(time.time())
    start = end - 1000 * MINUTES * 60
    r = requests.get(
        f"{BASE}/v2/history/candles",
        params={"symbol": SYMBOL, "resolution": RES, "start": start, "end": end},
        timeout=15,
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json()["result"]).sort_values("time").reset_index(drop=True)
    df["close"] = df["close"].astype(float)
    return df

def check_cross(prev_close, last_close, prev_line, last_line):
    if prev_close <= prev_line and last_close > last_line:
        return "BUY"
    if prev_close >= prev_line and last_close < last_line:
        return "SELL"
    return None

df = get_candles().iloc[:-1]  # closed candles only
df["ema"] = df["close"].ewm(span=LENGTH, adjust=False).mean()
df["ma"] = df["close"].rolling(LENGTH).mean()

# First run: save a starting point and stop
if not os.path.exists(STATE_FILE):
    with open(STATE_FILE, "w") as f:
        f.write(str(int(df.iloc[-1]["time"])))
    notify(f"Bot started: watching {SYMBOL} {RES} with MA {LENGTH} and EMA {LENGTH}")
    raise SystemExit

with open(STATE_FILE) as f:
    last_alert = int(f.read().strip())

newest = last_alert
for i in range(len(df) - 6, len(df)):  # check the last 6 closed candles
    prev, cur = df.iloc[i - 1], df.iloc[i]
    t = int(cur["time"])
    if t <= last_alert:
        continue
    signals = []
    s = check_cross(prev["close"], cur["close"], prev["ema"], cur["ema"])
    if s:
        signals.append(f"{s} (crossed EMA {LENGTH})")
    s = check_cross(prev["close"], cur["close"], prev["ma"], cur["ma"])
    if s:
        signals.append(f"{s} (crossed MA {LENGTH})")
    if signals:
        when = datetime.fromtimestamp(t + MINUTES * 60, IST).strftime("%d-%b %H:%M")
        notify(
            f"{SYMBOL} {RES}: " + " | ".join(signals)
            + f"\nclose={cur['close']}  EMA={round(cur['ema'],1)}  MA={round(cur['ma'],1)}"
            + f"\nCandle closed: {when} IST"
        )
        newest = max(newest, t)

if newest != last_alert:
    with open(STATE_FILE, "w") as f:
        f.write(str(newest))

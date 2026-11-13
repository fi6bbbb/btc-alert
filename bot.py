import os
import json
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

BASE = "https://api.india.delta.exchange"
SYMBOLS = ["BTCUSD", "ETHUSD"]
TIMEFRAMES = ["1m", "3m", "5m", "10m", "15m", "30m", "1h"]
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}
# How many recent closed candles to re-check each run (covers late runs)
LOOKBACK = {"1m": 15, "3m": 8, "5m": 6, "10m": 4, "15m": 3, "30m": 3, "1h": 3}
# Lines to watch: ("EMA" or "MA", length)
LINES = [("EMA", 500), ("MA", 500), ("MA", 200)]
CANDLES = 1000
STATE_FILE = "state.json"
IST = timezone(timedelta(hours=5, minutes=30))

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

def notify(text):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=15,
    )

def fetch(symbol, resolution, minutes, count):
    end = int(time.time())
    start = end - count * minutes * 60
    r = requests.get(
        f"{BASE}/v2/history/candles",
        params={"symbol": symbol, "resolution": resolution, "start": start, "end": end},
        timeout=20,
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json()["result"]).sort_values("time").reset_index(drop=True)
    df["close"] = df["close"].astype(float)
    return df

def get_closed_candles(symbol, tf):
    if tf == "10m":
        # Build 10m candles from closed 5m candles
        df = fetch(symbol, "5m", 5, 1200).iloc[:-1]
        last_end = int(df.iloc[-1]["time"]) + 300
        df["bucket"] = (df["time"] // 600) * 600
        out = df.groupby("bucket")["close"].last().reset_index()
        out = out.rename(columns={"bucket": "time"})
        out = out[out["time"] + 600 <= last_end]  # keep only finished 10m candles
        return out[["time", "close"]].reset_index(drop=True)
    return fetch(symbol, tf, TF_MINUTES[tf], CANDLES).iloc[:-1]  # closed candles only

def add_lines(df):
    for kind, n in LINES:
        col = f"{kind}{n}"
        if kind == "EMA":
            df[col] = df["close"].ewm(span=n, adjust=False).mean()
        else:
            df[col] = df["close"].rolling(n).mean()
    return df

def check_cross(prev_close, last_close, prev_line, last_line):
    if prev_close <= prev_line and last_close > last_line:
        return "BUY"
    if prev_close >= prev_line and last_close < last_line:
        return "SELL"
    return None

state = {}
if os.path.exists(STATE_FILE):
    with open(STATE_FILE) as f:
        state = json.load(f)

changed = False
started = []

for symbol in SYMBOLS:
    for tf in TIMEFRAMES:
        key = f"{symbol}_{tf}"
        try:
            df = add_lines(get_closed_candles(symbol, tf))

            # First time for this coin and timeframe: save a starting point, no alerts
            if key not in state:
                state[key] = int(df.iloc[-1]["time"])
                started.append(f"{symbol} {tf}")
                changed = True
                continue

            last_alert = state[key]
            newest = last_alert
            for i in range(len(df) - LOOKBACK[tf], len(df)):
                prev, cur = df.iloc[i - 1], df.iloc[i]
                t = int(cur["time"])
                if t <= last_alert:
                    continue
                signals = []
                for kind, n in LINES:
                    col = f"{kind}{n}"
                    s = check_cross(prev["close"], cur["close"], prev[col], cur[col])
                    if s:
                        signals.append(f"{s} (crossed {kind} {n})")
                if signals:
                    when = datetime.fromtimestamp(
                        t + TF_MINUTES[tf] * 60, IST
                    ).strftime("%d-%b %H:%M")
                    values = "  ".join(
                        f"{kind}{n}={round(cur[f'{kind}{n}'], 1)}" for kind, n in LINES
                    )
                    notify(
                        f"{symbol} {tf}: " + " | ".join(signals)
                        + f"\nclose={cur['close']}\n{values}"
                        + f"\nCandle closed: {when} IST"
                    )
                    newest = max(newest, t)
            if newest != last_alert:
                state[key] = newest
                changed = True
        except Exception as e:
            print(f"{key} error:", e)

if started:
    notify("Now watching: " + ", ".join(started))

if changed:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

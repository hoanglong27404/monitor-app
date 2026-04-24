import requests
import anthropic
import os
from datetime import datetime

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

def get_fpt_price():
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/FPT.VN?interval=1d&range=5d"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        result = data["chart"]["result"][0]
        meta   = result["meta"]
        price  = meta.get("regularMarketPrice", 0)
        ref    = meta.get("chartPreviousClose", 0)
        change = price - ref
        pct    = round((change / ref * 100), 2) if ref else 0
        return {"price": price, "ref": ref,
                "ceil": round(price * 1.07), "floor": round(price * 0.93),
                "change": change, "pct": pct, "success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_fpt_history():
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/FPT.VN?interval=1d&range=30d"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        quotes = result["indicators"]["quote"][0]
        closes = quotes.get("close", [])
        opens  = quotes.get("open",  [])
        highs  = quotes.get("high",  [])
        lows   = quotes.get("low",   [])
        history = []
        for i in range(len(timestamps)):
            c = closes[i] if i < len(closes) else None
            if c:
                history.append({
                    "date":  datetime.fromtimestamp(timestamps[i]).strftime("%d/%m/%Y"),
                    "open":  opens[i]  if i < len(opens)  else 0,
                    "close": c,
                    "high":  highs[i]  if i < len(highs)  else 0,
                    "low":   lows[i]   if i < len(lows)   else 0,
                })
        return history[-10:]
    except Exception:
        return []

def fmt(price):
    return f"{int(price):,}".replace(",", ".")

def trend_icon(pct):
    if pct > 1:  return "🚀"
    if pct > 0:  return "📈"
    if pct < -1: return "💥"
    if pct < 0:  return "📉"
    return "➡️"

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if len(text) > 4000:
        text = text[:3990] + "\n_(cat ngan)_"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})

def analyze_with_claude(current, history):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    hist = "\n".join([
        f"- {h['date']}: Open {fmt(h['open'])} | Close {fmt(h['close'])} | H {fmt(h['high'])} | L {fmt(h['low'])}"
        for h in history
    ]) if history else "Khong co du lieu"
    prompt = (
        "Ban la chuyen gia phan tich chung khoan Viet Nam. Phan tich FPT:\n"
        f"Gia: {fmt(current['price'])} VND | Thay doi: {current['change']:+,.0f} ({current['pct']:+.2f}%)\n"
        f"Tham chieu: {fmt(current['ref'])} | Tran: {fmt(current['ceil'])} | San: {fmt(current['floor'])}\n"
        f"Lich su 10 ngay:\n{hist}\n"
        "Viet bang tieng Viet, dung emoji, duoi 200 tu:\n"
        "1. Nhan xet hom nay\n2. Xu huong ngan han\n"
        "3. Du doan tham khao (ngay mai / tuan sau / thang sau)\n"
        "4. Rui ro can chu y\n"
        "Ghi ro chi la tham khao, khong phai loi khuyen dau tu."
    )
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

if __name__ == "__main__":
    now = datetime.now()
    label = "Ket thuc ca sang 11:30" if now.hour < 12 else "Ket thuc ca chieu 14:30"
    current = get_fpt_price()
    if not current["success"]:
        send_telegram(f"Khong lay duoc gia FPT: {current.get('error')}")
    else:
        icon = trend_icon(current["pct"])
        history = get_fpt_history()
        analysis = analyze_with_claude(current, history)
        msg = (f"{icon} *FPT - {label}*\n"
               f"Ngay: {now.strftime('%d/%m/%Y %H:%M')}\n\n"
               f"Gia: `{fmt(current['price'])}` VND\n"
               f"Thay doi: `{current['change']:+,.0f}` ({current['pct']:+.2f}%)\n"
               f"Tham chieu: `{fmt(current['ref'])}` | Tran: `{fmt(current['ceil'])}` | San: `{fmt(current['floor'])}`\n\n"
               f"---\n{analysis}")
        send_telegram(msg)
        print(f"OK: {fmt(current['price'])} VND ({current['pct']:+.2f}%)")

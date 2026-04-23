import requests
import anthropic
import os
from datetime import datetime

# Đọc từ GitHub Secrets — KHÔNG dùng config.py
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

def get_fpt_price():
    try:
        url = "https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term?ticker=FPT&type=stock&resolution=D&lookback=5"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        bars = data.get("data", [])
        if not bars:
            return {"success": False, "error": "Không có dữ liệu"}
        latest = bars[-1]
        prev   = bars[-2] if len(bars) >= 2 else latest
        price     = latest.get("close", 0)
        ref_price = prev.get("close", 0)
        change    = price - ref_price
        pct       = round((change / ref_price * 100), 2) if ref_price else 0
        return {
            "price": price, "ref": ref_price,
            "ceil": round(price * 1.07), "floor": round(price * 0.93),
            "change": change, "pct": pct,
            "date": latest.get("tradingDate", ""), "success": True
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_fpt_history():
    try:
        url = "https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term?ticker=FPT&type=stock&resolution=D&lookback=30"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        bars = data.get("data", [])
        recent = bars[-10:] if len(bars) >= 10 else bars
        return [{"date": b.get("tradingDate",""), "open": b.get("open",0),
                 "close": b.get("close",0), "high": b.get("high",0),
                 "low": b.get("low",0)} for b in recent]
    except:
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
        text = text[:3990] + "\n_(cắt ngắn)_"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    })

def analyze_with_claude(current, history):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    history_text = "\n".join([
        f"- {h['date']}: Open {fmt(h['open'])} | Close {fmt(h['close'])} | H {fmt(h['high'])} | L {fmt(h['low'])}"
        for h in history
    ]) if history else "Không có dữ liệu lịch sử"

    prompt = f"""Bạn là chuyên gia phân tích chứng khoán Việt Nam. Phân tích cổ phiếu FPT:

Giá hiện tại: {fmt(current['price'])} VNĐ
Thay đổi: {current['change']:+,} VNĐ ({current['pct']:+.2f}%)
Tham chiếu: {fmt(current['ref'])} | Trần: {fmt(current['ceil'])} | Sàn: {fmt(current['floor'])}

Lịch sử 10 ngày:
{history_text}

Viết phân tích ngắn bằng tiếng Việt, dùng emoji:
1. 📊 Nhận xét hôm nay
2. 📈 Xu hướng ngắn hạn
3. 🔮 Dự đoán tham khảo (ngày mai / tuần sau / tháng sau)
4. ⚠️ Rủi ro cần chú ý

Dưới 200 từ. Ghi rõ đây chỉ là tham khảo, không phải lời khuyên đầu tư."""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

if __name__ == "__main__":
    now = datetime.now()
    label = "Kết thúc ca sáng 11:30" if now.hour < 12 else "Kết thúc ca chiều 14:30"

    current = get_fpt_price()
    if not current["success"]:
        send_telegram(f"⚠️ Không lấy được giá FPT: `{current.get('error')}`")
    else:
        icon = trend_icon(current["pct"])
        history = get_fpt_history()
        analysis = analyze_with_claude(current, history)
        msg = (
            f"{icon} *FPT — {label}*\n"
            f"🕐 {now.strftime('%d/%m/%Y %H:%M')}\n\n"
            f"💰 Giá: `{fmt(current['price'])}` VNĐ\n"
            f"📊 Thay đổi: `{current['change']:+,}` ({current['pct']:+.2f}%)\n"
            f"📌 Tham chiếu: `{fmt(current['ref'])}` | Trần: `{fmt(current['ceil'])}` | Sàn: `{fmt(current['floor'])}`\n\n"
            f"---\n{analysis}"
        )
        send_telegram(msg)
        print(f"✅ Đã gửi báo cáo FPT: {fmt(current['price'])} VNĐ ({current['pct']:+.2f}%)")

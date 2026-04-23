# fpt_monitor.py
import anthropic
import requests
import json
from datetime import datetime, date
from apscheduler.schedulers.blocking import BlockingScheduler
import config

# ── Lấy giá FPT qua vnstock (không cần API key) ───────────────
def get_fpt_price():
    """Gọi API TCBS để lấy giá FPT realtime"""
    try:
        # TCBS public API — không cần auth
        url = "https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/second-tc?ticker=FPT&type=price"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()

        price     = data.get("p", 0)          # giá hiện tại
        ref_price = data.get("r", 0)           # giá tham chiếu
        ceil      = data.get("c", 0)           # giá trần
        floor     = data.get("f", 0)           # giá sàn
        change    = price - ref_price
        pct       = (change / ref_price * 100) if ref_price else 0

        return {
            "price":     price,
            "ref":       ref_price,
            "ceil":      ceil,
            "floor":     floor,
            "change":    change,
            "pct":       round(pct, 2),
            "success":   True
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# ── Lấy lịch sử giá 30 ngày để Claude phân tích ──────────────
def get_fpt_history():
    """Lấy lịch sử giá FPT 30 ngày gần nhất"""
    try:
        url = "https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term?ticker=FPT&type=stock&resolution=D&lookback=30"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        bars = data.get("data", [])

        # Lấy 10 ngày gần nhất để gửi cho Claude
        recent = bars[-10:] if len(bars) >= 10 else bars
        history = []
        for b in recent:
            history.append({
                "date":  b.get("tradingDate", ""),
                "open":  b.get("open", 0),
                "close": b.get("close", 0),
                "high":  b.get("high", 0),
                "low":   b.get("low", 0),
            })
        return history
    except Exception as e:
        return []

# ── Gửi Telegram ───────────────────────────────────────────────
def send_telegram(text):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    if len(text) > 4000:
        text = text[:3990] + "\n_(tin nhắn bị cắt)_"
    requests.post(url, json={
        "chat_id":    config.TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown"
    })

# ── Format giá đẹp ────────────────────────────────────────────
def fmt(price):
    return f"{int(price):,}".replace(",", ".")

def trend_icon(pct):
    if pct > 1:   return "🚀"
    if pct > 0:   return "📈"
    if pct < -1:  return "💥"
    if pct < 0:   return "📉"
    return "➡️"

# ── Claude phân tích + dự đoán ────────────────────────────────
def analyze_with_claude(current, history):
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    history_text = "\n".join([
        f"- {h['date']}: Open {fmt(h['open'])} | Close {fmt(h['close'])} | H {fmt(h['high'])} | L {fmt(h['low'])}"
        for h in history
    ]) if history else "Không có dữ liệu lịch sử"

    prompt = f"""Bạn là chuyên gia phân tích chứng khoán Việt Nam. Hãy phân tích cổ phiếu FPT dựa trên dữ liệu sau:

**Giá hiện tại:**
- Giá khớp: {fmt(current['price'])} VNĐ
- Tham chiếu: {fmt(current['ref'])} VNĐ
- Thay đổi: {current['change']:+,} VNĐ ({current['pct']:+.2f}%)
- Trần: {fmt(current['ceil'])} | Sàn: {fmt(current['floor'])}

**Lịch sử 10 ngày gần nhất:**
{history_text}

Hãy viết phân tích ngắn gọn bằng tiếng Việt gồm:
1. 📊 **Nhận xét hôm nay** (1-2 câu về diễn biến giá hôm nay)
2. 📈 **Xu hướng ngắn hạn** (dựa trên 10 ngày qua)
3. 🔮 **Dự đoán tham khảo** (ngày mai / tuần sau / tháng sau) — nêu rõ đây chỉ là nhận định tham khảo, KHÔNG phải lời khuyên đầu tư
4. ⚠️ **Rủi ro cần chú ý**

Viết ngắn gọn, dưới 200 từ, dùng emoji, phù hợp hiển thị trên Telegram."""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

# ── Giá snapshot để phát hiện biến động ──────────────────────
last_price = None
ALERT_THRESHOLD_PCT = 1.5  # cảnh báo khi biến động >= 1.5%

# ── Job báo cáo theo lịch ─────────────────────────────────────
def scheduled_report(label):
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    current = get_fpt_price()

    if not current["success"]:
        send_telegram(f"⚠️ Không lấy được giá FPT: `{current.get('error')}`")
        return

    icon = trend_icon(current["pct"])
    history = get_fpt_history()
    analysis = analyze_with_claude(current, history)

    msg = (
        f"{icon} *FPT — {label}*\n"
        f"🕐 {now}\n\n"
        f"💰 Giá: `{fmt(current['price'])}` VNĐ\n"
        f"📊 Thay đổi: `{current['change']:+,}` ({current['pct']:+.2f}%)\n"
        f"📌 Tham chiếu: `{fmt(current['ref'])}` | Trần: `{fmt(current['ceil'])}` | Sàn: `{fmt(current['floor'])}`\n\n"
        f"---\n{analysis}"
    )
    send_telegram(msg)

# ── Job cảnh báo biến động ────────────────────────────────────
def alert_check():
    global last_price
    current = get_fpt_price()
    if not current["success"]:
        return

    price = current["price"]

    # Lần đầu chạy
    if last_price is None:
        last_price = price
        return

    pct_change = abs((price - last_price) / last_price * 100) if last_price else 0

    if pct_change >= ALERT_THRESHOLD_PCT:
        direction = "tăng 🚀" if price > last_price else "giảm 💥"
        msg = (
            f"🔔 *CẢNH BÁO BIẾN ĐỘNG FPT*\n\n"
            f"Giá vừa {direction} `{pct_change:.2f}%`\n"
            f"• Trước: `{fmt(last_price)}` VNĐ\n"
            f"• Hiện tại: `{fmt(price)}` VNĐ\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        )
        send_telegram(msg)
        last_price = price  # reset mốc

# ── Khởi động ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 FPT Stock Monitor khởi động...")

    # Test ngay khi chạy
    scheduled_report("Khởi động")

    scheduler = BlockingScheduler(timezone="Asia/Ho_Chi_Minh")

    # ── Báo cáo kết thúc ca sáng (11:30) ──────────────────────
    scheduler.add_job(
        lambda: scheduled_report("Kết thúc ca sáng 11:30"),
        "cron",
        day_of_week="mon-fri",
        hour=11,
        minute=30
    )

    # ── Báo cáo kết thúc ca chiều (14:30) ─────────────────────
    scheduler.add_job(
        lambda: scheduled_report("Kết thúc ca chiều 14:30"),
        "cron",
        day_of_week="mon-fri",
        hour=14,
        minute=30
    )

    # ── Cảnh báo biến động mỗi 5 phút trong giờ giao dịch ─────
    # Ca sáng: 9:00 - 11:30
    scheduler.add_job(
        alert_check, "cron",
        day_of_week="mon-fri",
        hour="9-11",
        minute="*/5"
    )
    # Ca chiều: 13:00 - 14:30
    scheduler.add_job(
        alert_check, "cron",
        day_of_week="mon-fri",
        hour="13-14",
        minute="*/5"
    )

    print("✅ Scheduler chạy:")
    print("   📊 Báo cáo lúc 11:30 (kết ca sáng) và 14:30 (kết ca chiều) — T2 đến T6")
    print(f"   ⚡ Cảnh báo biến động >= {ALERT_THRESHOLD_PCT}% mỗi 5 phút trong giờ GD")
    scheduler.start()
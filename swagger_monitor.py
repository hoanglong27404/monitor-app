# swagger_monitor.py
import requests
import json
import hashlib
import anthropic
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
import config

SWAGGER_URL =  "https://eleclab-api.onrender.com/swagger-json"
SNAPSHOT_FILE = "api_snapshot.json"

# ── Lấy spec hiện tại ──────────────────────────────────────────
def fetch_spec():
    r = requests.get(SWAGGER_URL, timeout=30)
    r.raise_for_status()
    return r.json()

# ── Load / Save snapshot ───────────────────────────────────────
def load_snapshot():
    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None

def save_snapshot(spec):
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)

# ── Trích xuất danh sách endpoint gọn ─────────────────────────
def extract_endpoints(spec):
    """
    Trả về dict: { "GET /api/users": {summary, tags, params, responses} }
    """
    endpoints = {}
    for path, methods in spec.get("paths", {}).items():
        for method, details in methods.items():
            if method in ["get","post","put","patch","delete","options"]:
                key = f"{method.upper()} {path}"
                # Rút gọn responses: chỉ lấy status codes
                response_codes = list(details.get("responses", {}).keys())
                # Rút gọn params: chỉ lấy tên + required
                params = [
                    {"name": p.get("name"), "required": p.get("required", False), "in": p.get("in")}
                    for p in details.get("parameters", [])
                ]
                # requestBody schema keys
                req_body = None
                if "requestBody" in details:
                    content = details["requestBody"].get("content", {})
                    if "application/json" in content:
                        schema = content["application/json"].get("schema", {})
                        req_body = list(schema.get("properties", {}).keys()) if "properties" in schema else schema.get("$ref")

                endpoints[key] = {
                    "summary": details.get("summary", ""),
                    "tags": details.get("tags", []),
                    "params": params,
                    "response_codes": response_codes,
                    "request_body_fields": req_body,
                    "security": bool(details.get("security")),
                }
    return endpoints

# ── So sánh 2 snapshot ─────────────────────────────────────────
def compare_specs(old_spec, new_spec):
    old_eps = extract_endpoints(old_spec)
    new_eps = extract_endpoints(new_spec)

    added   = {k: v for k, v in new_eps.items() if k not in old_eps}
    removed = {k: v for k, v in old_eps.items() if k not in new_eps}
    changed = {}

    for key in old_eps:
        if key in new_eps and old_eps[key] != new_eps[key]:
            old_val = old_eps[key]
            new_val = new_eps[key]
            diff = {}
            for field in set(list(old_val.keys()) + list(new_val.keys())):
                if old_val.get(field) != new_val.get(field):
                    diff[field] = {
                        "before": old_val.get(field),
                        "after":  new_val.get(field)
                    }
            if diff:
                changed[key] = diff

    # So sánh components/schemas
    old_schemas = set(old_spec.get("components", {}).get("schemas", {}).keys())
    new_schemas = set(new_spec.get("components", {}).get("schemas", {}).keys())
    added_schemas   = new_schemas - old_schemas
    removed_schemas = old_schemas - new_schemas

    return {
        "endpoints_added":   added,
        "endpoints_removed": removed,
        "endpoints_changed": changed,
        "schemas_added":     list(added_schemas),
        "schemas_removed":   list(removed_schemas),
        "has_changes": bool(added or removed or changed or added_schemas or removed_schemas)
    }

# ── Dùng Claude phân tích diff ────────────────────────────────
def analyze_diff_with_claude(diff):
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    prompt = f"""Bạn là chuyên gia phân tích API. Dưới đây là sự thay đổi trong Swagger spec của hệ thống ElecLab:

{json.dumps(diff, ensure_ascii=False, indent=2)}

Hãy viết báo cáo ngắn gọn cho developer bằng tiếng Việt, dùng emoji:
1. 🆕 Endpoints mới (nếu có) — nêu rõ endpoint và tác dụng
2. ❌ Endpoints bị xóa (nếu có) — cảnh báo breaking change
3. ✏️ Endpoints thay đổi (nếu có) — nêu rõ thay đổi gì (thêm/bớt param, thay đổi response codes...)
4. 📦 Schemas mới/xóa (nếu có)
5. ⚠️ Đánh giá mức độ ảnh hưởng: THẤP / TRUNG BÌNH / CAO

Nếu không có thay đổi quan trọng, chỉ cần nói "Không có thay đổi đáng kể."
Giữ ngắn gọn, dưới 300 từ."""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

# ── Gửi Telegram ───────────────────────────────────────────────
def send_telegram(text, parse_mode="Markdown"):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram giới hạn 4096 ký tự
    if len(text) > 4000:
        text = text[:3990] + "\n\n_(tin nhắn bị cắt ngắn)_"
    requests.post(url, json={
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode
    })

# ── Tạo báo cáo health check ──────────────────────────────────
def health_check():
    try:
        r = requests.get("https://eleclab-api.onrender.com/api/health", timeout=10)
        data = r.json()
        status = data.get("status", "unknown")
        uptime = round(data.get("uptime", 0) / 3600, 1)
        db = data.get("database", "unknown")
        return f"✅ *API Health OK*\nStatus: `{status}` | DB: `{db}` | Uptime: `{uptime}h`"
    except Exception as e:
        return f"🔴 *API Health FAILED*\n`{str(e)}`"

# ── Job chính ──────────────────────────────────────────────────
def monitor_job():
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    print(f"[{now}] Đang kiểm tra API spec...")

    try:
        new_spec = fetch_spec()
    except Exception as e:
        send_telegram(f"⚠️ *Không thể fetch Swagger spec*\n`{str(e)}`")
        return

    old_spec = load_snapshot()

    if old_spec is None:
        # Lần đầu chạy — lưu snapshot và báo cáo tổng quan
        save_snapshot(new_spec)
        eps = extract_endpoints(new_spec)
        schemas = new_spec.get("components", {}).get("schemas", {})
        msg = (
            f"🚀 *ElecLab API Monitor — Khởi động*\n"
            f"🕐 {now}\n\n"
            f"📊 Tổng quan ban đầu:\n"
            f"• Endpoints: `{len(eps)}`\n"
            f"• Schemas: `{len(schemas)}`\n"
            f"• Tags: `{len(set(t for ep in extract_endpoints(new_spec).values() for t in ep['tags']))}`\n\n"
            f"✅ Snapshot đã lưu. Từ đây mọi thay đổi sẽ được thông báo."
        )
        send_telegram(msg)
        print("  → Snapshot khởi tạo xong.")
        return

    diff = compare_specs(old_spec, new_spec)

    if not diff["has_changes"]:
        print("  → Không có thay đổi.")
        return

    print(f"  → Phát hiện thay đổi! Gọi Claude phân tích...")
    analysis = analyze_diff_with_claude(diff)

    # Thống kê nhanh
    stats = (
        f"📋 *ElecLab API — Phát hiện thay đổi*\n"
        f"🕐 {now}\n\n"
        f"• 🆕 Thêm: `{len(diff['endpoints_added'])}` endpoints\n"
        f"• ❌ Xóa: `{len(diff['endpoints_removed'])}` endpoints\n"
        f"• ✏️ Sửa: `{len(diff['endpoints_changed'])}` endpoints\n"
        f"• 📦 Schema: +`{len(diff['schemas_added'])}` / -`{len(diff['schemas_removed'])}`\n\n"
    )

    send_telegram(stats + analysis)
    save_snapshot(new_spec)
    print("  → Đã gửi Telegram và cập nhật snapshot.")

# ── Daily health report ────────────────────────────────────────
def daily_report():
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    spec = load_snapshot()
    if not spec:
        return

    eps = extract_endpoints(spec)
    tags = {}
    for ep_data in eps.values():
        for tag in ep_data["tags"]:
            tags[tag] = tags.get(tag, 0) + 1

    health = health_check()
    top_tags = sorted(tags.items(), key=lambda x: -x[1])[:5]
    tags_str = "\n".join(f"  • `{t}`: {c} endpoints" for t, c in top_tags)

    msg = (
        f"📅 *Báo cáo hàng ngày — ElecLab API*\n"
        f"🕐 {now}\n\n"
        f"{health}\n\n"
        f"📊 Tổng: `{len(eps)}` endpoints\n"
        f"🏷️ Top modules:\n{tags_str}"
    )
    send_telegram(msg)

# ── Khởi động ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 ElecLab API Monitor đang khởi động...")
    monitor_job()  # Chạy ngay lần đầu

    scheduler = BlockingScheduler(timezone="Asia/Ho_Chi_Minh")
    scheduler.add_job(monitor_job,   "interval", minutes=config.CHECK_INTERVAL_MINUTES, id="monitor")
    scheduler.add_job(daily_report,  "cron",     hour=8, minute=0, id="daily")
    print(f"✅ Scheduler chạy. Check mỗi {config.CHECK_INTERVAL_MINUTES} phút.")
    scheduler.start()
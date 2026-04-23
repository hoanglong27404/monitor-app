import requests
import json
import anthropic
import os
from datetime import datetime

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")
SWAGGER_URL        = "https://eleclab-api.onrender.com/swagger-json"
SNAPSHOT_FILE      = "api_snapshot.json"


def fetch_spec():
    r = requests.get(SWAGGER_URL, timeout=30)
    r.raise_for_status()
    return r.json()


def load_snapshot():
    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_snapshot(spec):
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)


def extract_endpoints(spec):
    endpoints = {}
    for path, methods in spec.get("paths", {}).items():
        for method, details in methods.items():
            if method in ["get", "post", "put", "patch", "delete", "options"]:
                key = f"{method.upper()} {path}"
                response_codes = list(details.get("responses", {}).keys())
                params = [
                    {"name": p.get("name"), "required": p.get("required", False), "in": p.get("in")}
                    for p in details.get("parameters", [])
                ]
                req_body = None
                if "requestBody" in details:
                    content = details["requestBody"].get("content", {})
                    if "application/json" in content:
                        schema = content["application/json"].get("schema", {})
                        req_body = list(schema.get("properties", {}).keys()) if "properties" in schema else schema.get("$ref")

                endpoints[key] = {
                    "summary":             details.get("summary", ""),
                    "tags":                details.get("tags", []),
                    "params":              params,
                    "response_codes":      response_codes,
                    "request_body_fields": req_body,
                    "security":            bool(details.get("security")),
                }
    return endpoints


def compare_specs(old_spec, new_spec):
    old_eps = extract_endpoints(old_spec)
    new_eps = extract_endpoints(new_spec)

    added   = {k: v for k, v in new_eps.items() if k not in old_eps}
    removed = {k: v for k, v in old_eps.items() if k not in new_eps}
    changed = {}

    for key in old_eps:
        if key in new_eps and old_eps[key] != new_eps[key]:
            old_val, new_val = old_eps[key], new_eps[key]
            diff = {
                field: {"before": old_val.get(field), "after": new_val.get(field)}
                for field in set(list(old_val.keys()) + list(new_val.keys()))
                if old_val.get(field) != new_val.get(field)
            }
            if diff:
                changed[key] = diff

    old_schemas = set(old_spec.get("components", {}).get("schemas", {}).keys())
    new_schemas = set(new_spec.get("components", {}).get("schemas", {}).keys())

    return {
        "endpoints_added":   added,
        "endpoints_removed": removed,
        "endpoints_changed": changed,
        "schemas_added":     list(new_schemas - old_schemas),
        "schemas_removed":   list(old_schemas - new_schemas),
        "has_changes":       bool(added or removed or changed or (new_schemas - old_schemas) or (old_schemas - new_schemas))
    }


def analyze_diff_with_claude(diff):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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


def send_telegram(text, parse_mode="Markdown"):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if len(text) > 4000:
        text = text[:3990] + "\n\n_(tin nhắn bị cắt ngắn)_"
    requests.post(url, json={
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": parse_mode
    })


def health_check():
    try:
        r = requests.get("https://eleclab-api.onrender.com/api/health", timeout=10)
        data = r.json()
        status = data.get("status", "unknown")
        uptime = round(data.get("uptime", 0) / 3600, 1)
        db     = data.get("database", "unknown")
        return f"✅ *API Health OK*\nStatus: `{status}` | DB: `{db}` | Uptime: `{uptime}h`"
    except Exception as e:
        return f"🔴 *API Health FAILED*\n`{str(e)}`"


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
        save_snapshot(new_spec)
        eps     = extract_endpoints(new_spec)
        schemas = new_spec.get("components", {}).get("schemas", {})
        msg = (
            f"🚀 *ElecLab API Monitor — Khởi động*\n"
            f"🕐 {now}\n\n"
            f"📊 Tổng quan ban đầu:\n"
            f"• Endpoints: `{len(eps)}`\n"
            f"• Schemas: `{len(schemas)}`\n"
            f"• Tags: `{len(set(t for ep in eps.values() for t in ep['tags']))}`\n\n"
            f"✅ Snapshot đã lưu. Từ đây mọi thay đổi sẽ được thông báo."
        )
        send_telegram(msg)
        print("  → Snapshot khởi tạo xong.")
        return

    diff = compare_specs(old_spec, new_spec)

    if not diff["has_changes"]:
        print("  → Không có thay đổi.")
        return

    print("  → Phát hiện thay đổi! Gọi Claude phân tích...")
    analysis = analyze_diff_with_claude(diff)

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


if __name__ == "__main__":
    monitor_job()  # Chỉ chạy 1 lần rồi thoát

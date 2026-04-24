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
            try:
                            r = requests.get(SWAGGER_URL, timeout=15)
                            return r.json()
except Exception as e:
        return None

def load_snapshot():
            try:
                            return json.load(open(SNAPSHOT_FILE))
                        except:
        return None

def save_snapshot(spec):
            json.dump(spec, open(SNAPSHOT_FILE, 'w'), indent=2)

def extract_endpoints(spec):
            endpoints = {}
            for path, methods in spec.get('paths', {}).items():
                            for method, info in methods.items():
                                                if method in ['get','post','put','delete','patch']:
                                                                        key = f"{method.upper()} {path}"
                                                                        endpoints[key] = {
                                                                            'summary': info.get('summary',''),
                                                                            'tags': info.get('tags',[]),
                                                                            'params': [p.get('name') for p in info.get('parameters',[])]
                                                                        }
                                                            return endpoints

def compare_specs(old_spec, new_spec):
            old_ep = extract_endpoints(old_spec)
            new_ep = extract_endpoints(new_spec)
            old_keys = set(old_ep.keys())
            new_keys = set(new_ep.keys())
            added   = new_keys - old_keys
            removed = old_keys - new_keys
            changed = {k for k in old_keys & new_keys if old_ep[k] != new_ep[k]}
            old_schemas = set(old_spec.get('components',{}).get('schemas',{}).keys())
            new_schemas = set(new_spec.get('components',{}).get('schemas',{}).keys())
            return {
                'added':   sorted(added),
                'removed': sorted(removed),
                'changed': sorted(changed),
                'schemas_added':   sorted(new_schemas - old_schemas),
                'schemas_removed': sorted(old_schemas - new_schemas),
            }

def send_telegram(text):
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            if len(text) > 4000:
                            text = text[:3990] + "\n_(cat ngan)_"
                        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})

def analyze_diff_with_claude(diff):
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    details = ""
    if diff['added']:
                    details += "Them:\n" + "\n".join(f"  + {e}" for e in diff['added'][:5]) + "\n"
                if diff['removed']:
                                details += "Xoa:\n" + "\n".join(f"  - {e}" for e in diff['removed'][:5]) + "\n"
                            if diff['changed']:
                                            details += "Sua:\n" + "\n".join(f"  ~ {e}" for e in diff['changed'][:5]) + "\n"
                                        prompt = (
                                                        "Ban la chuyen gia phan tich API. Day la su thay doi trong Swagger spec cua ElecLab:\n"
                                                        f"Them: {len(diff['added'])} | Xoa: {len(diff['removed'])} | Sua: {len(diff['changed'])}\n"
                                                        f"{details}\n"
                                                        "Phan tich ngan gon bang tieng Viet:\n"
                                                        "1. Muc do anh huong (CAO/TRUNG BINH/THAP)\n"
                                                        "2. Co breaking change khong?\n"
                                                        "3. Frontend can lam gi?\n"
                                                        "Giu duoi 200 tu."
                                        )
    msg = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

if __name__ == "__main__":
            now = datetime.now().strftime("%d/%m/%Y %H:%M")
    new_spec = fetch_spec()
    if not new_spec:
                    send_telegram(f"Khong the fetch Swagger spec tu {SWAGGER_URL}")
else:
        old_snapshot = load_snapshot()
        if not old_snapshot:
                            save_snapshot(new_spec)
                            ep_count = len(extract_endpoints(new_spec))
                            schemas  = len(new_spec.get('components',{}).get('schemas',{}))
                            tags     = len(new_spec.get('tags',[]))
                            msg = (
                                f"ElecLab API Monitor - Khoi dong\n"
                                f"Ngay: {now}\n\n"
                                f"Tong quan:\n"
                                f"Endpoints: {ep_count}\n"
                                f"Schemas: {schemas}\n"
                                f"Tags: {tags}\n\n"
                                f"Snapshot da luu. Tu day moi thay doi se duoc thong bao."
                            )
                            send_telegram(msg)
                            print(f"OK: {ep_count} endpoints")
else:
            diff = compare_specs(old_snapshot, new_spec)
            total = len(diff['added']) + len(diff['removed']) + len(diff['changed'])
            if total == 0 and not diff['schemas_added'] and not diff['schemas_removed']:
                                    print("Khong co thay doi")
else:
                save_snapshot(new_spec)
                        analysis = analyze_diff_with_claude(diff)
                lines = [f"ElecLab API - Phat hien thay doi!", f"Ngay: {now}\n"]
                if diff['added']:   lines.append(f"Them: {len(diff['added'])} endpoints")
                                        if diff['removed']: lines.append(f"Xoa: {len(diff['removed'])} endpoints")
                                                                if diff['changed']: lines.append(f"Sua: {len(diff['changed'])} endpoints")
                                                                                        if diff['schemas_added']:   lines.append(f"Schema moi: +{len(diff['schemas_added'])}")
                                                                                                                if diff['schemas_removed']: lines.append(f"Schema xoa: -{len(diff['schemas_removed'])}")
                        lines.append(f"\n---\n{analysis}")
                send_telegram("\n".join(lines))
                print(f"OK: {total} thay doi")

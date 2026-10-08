import os, re, sys, json, time, requests
from playwright.sync_api import sync_playwright

PROVINCES = ["songkhla", "yala", "nakhonsithammarat", "suratthani", "chumphon"]
LINE_TOKEN = os.environ["LINE_TOKEN"]
LINE_TO = os.environ["LINE_TO"]
REPO = os.environ["GITHUB_REPOSITORY"]
STAMP = time.strftime("%Y%m%d-%H%M")
OUT = "shots"

def overflow_count(page) -> int:
    # Adjust after inspecting the real page (see note below)
    text = page.inner_text("body")
    m = re.search(r"น้ำล้นตลิ่ง\D{0,40}?(\d+)\s*สถานี", text, re.S)
    return int(m.group(1)) if m else 0

def capture():
    os.makedirs(OUT, exist_ok=True)
    names = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000},
                                locale="th-TH", timezone_id="Asia/Bangkok")
        for prov in PROVINCES:
            base = f"https://{prov}.thaiwater.net"
            page.goto(f"{base}/dashboard", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)
            n = f"{STAMP}_{prov}_dashboard.png"
            page.screenshot(path=f"{OUT}/{n}", full_page=True)
            names.append(n)
            if overflow_count(page) > 0:
                page.goto(f"{base}/wl", wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)
                n = f"{STAMP}_{prov}_wl.png"
                page.screenshot(path=f"{OUT}/{n}", full_page=True)
                names.append(n)
        browser.close()
    json.dump(names, open(f"{OUT}/manifest.json", "w"))

def send():
    names = json.load(open(f"{OUT}/manifest.json"))
    urls = [f"https://raw.githubusercontent.com/{REPO}/images/{n}" for n in names]
    for i in range(0, len(urls), 5):  # max 5 messages per push
        msgs = [{"type": "image", "originalContentUrl": u, "previewImageUrl": u}
                for u in urls[i:i+5]]
        r = requests.post("https://api.line.me/v2/bot/message/push",
                          headers={"Authorization": f"Bearer {LINE_TOKEN}"},
                          json={"to": LINE_TO, "messages": msgs})
        r.raise_for_status()

if __name__ == "__main__":
    {"capture": capture, "send": send}[sys.argv[1]]()

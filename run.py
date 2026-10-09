import os, re, sys, json, time, requests
from playwright.sync_api import sync_playwright

PROVINCES = ["songkhla", "yala", "nakhonsithammarat", "suratthani", "chumphon"]
LINE_TOKEN = os.environ["LINE_TOKEN"]
LINE_TO = os.environ["LINE_TO"]
REPO = os.environ["GITHUB_REPOSITORY"]
STAMP = time.strftime("%Y%m%d-%H%M")
OUT = "shots"
FORCE_WL = os.environ.get("FORCE_WL") == "1"

SEL = ("#dashboard > div > div.mt-4 > div > div:nth-child(2) > div:nth-child(2) "
       "> div > div > div > div:nth-child(2) > table > tbody > tr:nth-child(1) "
       "> td:nth-child(3) > div")

def to_int(s):
    m = re.search(r"\d[\d,]*", s or "")
    return int(m.group().replace(",", "")) if m else 0

def overflow_count(page) -> int:
    # 1) Find the table row containing "น้ำล้นตลิ่ง" and read its 3rd cell
    try:
        page.wait_for_selector("table tbody tr", timeout=20000)
        for row in page.query_selector_all("table tbody tr"):
            if "น้ำล้นตลิ่ง" in row.inner_text():
                cells = row.query_selector_all("td")
                if len(cells) >= 3:
                    return to_int(cells[2].inner_text())
    except Exception:
        pass
    # 2) Fallback: the exact selector you sent
    try:
        page.wait_for_selector(SEL, timeout=20000)
        return to_int(page.inner_text(SEL))
    except Exception:
        return 0

def hide_cookie(page):
    # Try clicking "ยอมรับ" first
    try:
        page.get_by_text("ยอมรับ", exact=True).first.click(timeout=3000)
    except Exception:
        pass
    # Then remove the banner from the page if it is still there
    page.evaluate("""() => {
      document.querySelectorAll('body *').forEach(el => {
        if (getComputedStyle(el).position === 'fixed' &&
            (el.innerText || '').includes('นโยบายและคำประกาศ')) el.remove();
      });
    }""")
    page.wait_for_timeout(500)

def set_rows_25(page):
    try:
        page.get_by_text("แสดงผลหน้าละ").wait_for(timeout=20000)
        # click the dropdown that shows "10" next to the label
        page.locator("xpath=//*[contains(text(),'แสดงผลหน้าละ')]"
                     "/following::*[normalize-space(text())='10'][1]").click(timeout=5000)
        page.wait_for_timeout(500)
        # choose "25" from the opened list
        try:
            page.get_by_role("option", name="25", exact=True).click(timeout=3000)
        except Exception:
            page.locator("[role=listbox] >> text=25").first.click(timeout=3000)
        page.wait_for_timeout(3000)   # wait for the table to reload
    except Exception as e:
        print("set_rows_25 failed:", e)

def go_next_page(page) -> bool:
    """Click the next-page arrow in the table footer and confirm it moved to page 2."""
    candidates = [
        "table tfoot button:nth-child(3)",             # 3rd button = next
        "table tfoot button[aria-label='Next Page']",  # Material-UI default label
        "table tfoot button[title='Next Page']",
    ]
    for sel in candidates:
        try:
            btn = page.locator(sel).first
            btn.scroll_into_view_if_needed(timeout=5000)
            btn.click(timeout=5000)
            # verify: the footer should now say "26-34", not "1-25"
            page.get_by_text(re.compile(r"26\s*-\s*\d+\s*of")).wait_for(timeout=10000)
            page.wait_for_timeout(1500)
            return True
        except Exception as e:
            print(f"go_next_page: {sel} failed: {e}")
    return False

def shoot(page, names, filename):
    page.evaluate("window.scrollTo(0, 0)")  # fixes header/sidebar position
    page.wait_for_timeout(800)
    page.screenshot(path=f"{OUT}/{filename}", full_page=True)
    names.append(filename)

def capture():
    os.makedirs(OUT, exist_ok=True)
    names = []
    force = os.environ.get("FORCE_WL") == "1"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000},
                                locale="th-TH", timezone_id="Asia/Bangkok")
        for prov in PROVINCES:
            base = f"https://{prov}.thaiwater.net"

            # dashboard
            page.goto(f"{base}/dashboard", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)
            hide_cookie(page)
            shoot(page, names, f"{STAMP}_{prov}_dashboard.png")

            # water level pages, only if overflow count > 0
            if force or overflow_count(page) > 0:
                page.goto(f"{base}/wl", wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)
                hide_cookie(page)
                set_rows_25(page)
                shoot(page, names, f"{STAMP}_{prov}_wl.png")

                # Surat Thani: extra page (stations 26-34)
                if prov == "suratthani":
                    if go_next_page(page):
                        hide_cookie(page)
                        shoot(page, names, f"{STAMP}_{prov}_wl2.png")
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

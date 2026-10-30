import os, re, sys, json, time, requests
from playwright.sync_api import sync_playwright

PROVINCES = ["songkhla", "yala", "nakhonsithammarat", "suratthani", "chumphon",
             "ranong", "phuket", "krabi", "phangnga"]
LINE_TOKEN = os.environ["LINE_TOKEN"]
LINE_TO = os.environ["LINE_TO"]
REPO = os.environ["GITHUB_REPOSITORY"]
from datetime import datetime, timezone, timedelta
TH = timezone(timedelta(hours=7))
STAMP = datetime.now(TH).strftime("%Y%m%d-%H%M")
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

def footer_text(page):
    try:
        return page.get_by_text(re.compile(r"\d+\s*-\s*\d+\s*of\s*\d+")).first.inner_text()
    except Exception:
        return ""

def go_next_page(page) -> bool:
    """Click next-page and confirm the footer range changed (e.g. 1-25 -> 26-34)."""
    before = footer_text(page)
    candidates = [
        "table tfoot button:nth-child(3)",
        "table tfoot button[aria-label='Next Page']",
        "table tfoot button[title='Next Page']",
    ]
    for sel in candidates:
        try:
            btn = page.locator(sel).first
            if btn.is_disabled():
                return False              # already on the last page
            btn.scroll_into_view_if_needed(timeout=5000)
            btn.click(timeout=5000)
            for _ in range(20):           # wait up to 10 s for the table to change
                page.wait_for_timeout(500)
                if footer_text(page) != before:
                    page.wait_for_timeout(1500)
                    return True
        except Exception as e:
            print(f"go_next_page: {sel} failed: {e}")
    return False

from PIL import Image

def make_preview(src, dst, width=400):
    im = Image.open(src)
    h = int(im.height * width / im.width)
    im.resize((width, h)).convert("RGB").save(dst, "JPEG", quality=70)

def shoot(page, names, filename):
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(800)
    png = f"{OUT}/{filename}"
    page.screenshot(path=png, full_page=True)
    base = filename[:-4]
    Image.open(png).convert("RGB").save(f"{OUT}/{base}.jpg", "JPEG", quality=80)
    make_preview(png, f"{OUT}/{base}_prev.jpg")
    os.remove(png)
    names.append(base)

def open_page(page, url, tries=2):
    for i in range(tries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # some pages never go idle, that's OK
            page.wait_for_timeout(3000)
            return True
        except Exception as e:
            print(f"open_page failed ({i+1}/{tries}): {url} -> {e}")
            page.wait_for_timeout(5000)
    return False

def push_text(text):
    r = requests.post("https://api.line.me/v2/bot/message/push",
                      headers={"Authorization": f"Bearer {LINE_TOKEN}"},
                      json={"to": LINE_TO,
                            "messages": [{"type": "text", "text": text}]})
    r.raise_for_status()

def notify_failures(failed):
    if not failed:
        return
    lines = [f"⚠️ thaiwater: ดึงข้อมูลไม่สำเร็จ ({datetime.now(TH).strftime('%d/%m/%Y %H:%M')} น.)"]
    lines += [f"- {prov}: {reason}" for prov, reason in failed]
    try:
        push_text("\n".join(lines))
    except Exception as e:
        print("notify_failures failed:", e)

def trim_rows(page, keep):
    """Hide table rows after the first `keep` rows (overflow stations come first)."""
    page.evaluate("""(keep) => {
      const rows = document.querySelectorAll('table:has(tfoot) tbody tr');
      rows.forEach((r, i) => { r.style.display = i < keep ? '' : 'none'; });
    }""", keep)
    page.wait_for_timeout(500)

def capture():
    os.makedirs(OUT, exist_ok=True)
    names = []
    failed = []
    force = os.environ.get("FORCE_WL") == "1"
    PER_PAGE = 25
    MAX_PAGES = 8
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000},
                                locale="th-TH", timezone_id="Asia/Bangkok")
        for prov in PROVINCES:
            base = f"https://{prov}.thaiwater.net"
            try:
                if not open_page(page, f"{base}/dashboard"):
                    print(f"SKIP {prov}: dashboard not loaded")
                    failed.append((prov, "dashboard เปิดไม่ได้"))
                    continue
                hide_cookie(page)
                shoot(page, names, f"{STAMP}_{prov}_dashboard.png")

                n_over = overflow_count(page)
                print(f"{prov}: overflow stations = {n_over}")

                if force or n_over > 0:
                    # in test mode with 0 overflow, show a full first page
                    n_show = n_over if n_over > 0 else PER_PAGE
                    n_pages = min(-(-n_show // PER_PAGE), MAX_PAGES)  # ceil

                    if not open_page(page, f"{base}/wl"):
                        print(f"SKIP {prov}: wl not loaded")
                        failed.append((prov, "หน้า /wl เปิดไม่ได้"))
                        continue
                    hide_cookie(page)
                    set_rows_25(page)

                    for pn in range(1, n_pages + 1):
                        if pn > 1:
                            if not go_next_page(page):
                                failed.append((prov, f"หน้า /wl ชุดที่ {pn} ไม่สำเร็จ"))
                                break
                            hide_cookie(page)
                        keep = min(PER_PAGE, n_show - PER_PAGE * (pn - 1))
                        trim_rows(page, keep)
                        shoot(page, names, f"{STAMP}_{prov}_wl{pn}.png")
                    print(f"{prov}: wl pages = {n_pages}")
            except Exception as e:
                print(f"ERROR {prov}: {e}")
                failed.append((prov, "เกิดข้อผิดพลาด"))
                continue
        browser.close()
    json.dump(names, open(f"{OUT}/manifest.json", "w"))
    json.dump(failed, open(f"{OUT}/failed.json", "w"))

def send():
    names = json.load(open(f"{OUT}/manifest.json"))
    failed = json.load(open(f"{OUT}/failed.json"))
    urls = [(f"https://raw.githubusercontent.com/{REPO}/images/{n}.jpg",
         f"https://raw.githubusercontent.com/{REPO}/images/{n}_prev.jpg") for n in names]
    for i in range(0, len(urls), 5):  # max 5 messages per push
        msgs = [{"type": "image", "originalContentUrl": u, "previewImageUrl": p}
                for u, p in urls[i:i+5]]
        r = requests.post("https://api.line.me/v2/bot/message/push",
                          headers={"Authorization": f"Bearer {LINE_TOKEN}"},
                          json={"to": LINE_TO, "messages": msgs})
        r.raise_for_status()
    notify_failures([tuple(x) for x in failed])

if __name__ == "__main__":
    {"capture": capture, "send": send}[sys.argv[1]]()

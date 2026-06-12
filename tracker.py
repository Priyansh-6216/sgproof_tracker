"""
SGProof Deal Tracker
====================
Logs into shop.sgproof.com, checks prices on your tracked products,
saves history to CSV, and writes deals_today.html — just open it in
your browser to see what's worth buying today.

Setup:
  1. pip install playwright python-dotenv
  2. playwright install chromium
  3. Copy .env.example to .env and fill in your SGProof login
  4. Edit products.json with your products
  5. Run: python tracker.py
  6. Open deals_today.html in your browser
"""

import json, csv, os, sys, re
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── CONFIG ──────────────────────────────────────────────────────────────────

SGPROOF_EMAIL    = os.getenv("SGPROOF_EMAIL", "")
SGPROOF_PASSWORD = os.getenv("SGPROOF_PASSWORD", "")

PRODUCTS_FILE    = Path("products.json")
HISTORY_FILE     = Path("price_history.csv")
DEALS_HTML_FILE  = Path("deals_today.html")
LOG_FILE         = Path("tracker.log")

ALERT_THRESHOLD_PCT = 3.0   # alert if price drops this % or more

# ─── LOGGING ─────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

# ─── PRODUCTS ────────────────────────────────────────────────────────────────

def load_products():
    if not PRODUCTS_FILE.exists():
        log("products.json not found — creating example file.")
        example = [
            {"name": "Tito's Handmade Vodka 1.75L",      "url": "https://shop.sgproof.com/sgws/en/usd/search?q=tito%27s+1.75",       "category": "Vodka"},
            {"name": "Patron Silver Tequila 750ml",        "url": "https://shop.sgproof.com/sgws/en/usd/search?q=patron+silver+750",   "category": "Tequila"},
            {"name": "Hennessy VS Cognac 750ml",           "url": "https://shop.sgproof.com/sgws/en/usd/search?q=hennessy+vs+750",     "category": "Cognac"},
            {"name": "Jack Daniel's Tennessee Whiskey 1L", "url": "https://shop.sgproof.com/sgws/en/usd/search?q=jack+daniels+1L",     "category": "Whiskey"},
            {"name": "Don Julio Blanco 750ml",             "url": "https://shop.sgproof.com/sgws/en/usd/search?q=don+julio+blanco+750","category": "Tequila"},
        ]
        PRODUCTS_FILE.write_text(json.dumps(example, indent=2))
        log("Example products.json created. Edit it and re-run.")
        return example
    return json.loads(PRODUCTS_FILE.read_text())

# ─── HISTORY ─────────────────────────────────────────────────────────────────

HISTORY_FIELDS = ["date","name","price","deal_text","unit_count","per_unit"]

def load_history():
    history = {}
    if not HISTORY_FILE.exists():
        return history
    with open(HISTORY_FILE, newline="") as f:
        for row in csv.DictReader(f):
            history.setdefault(row["name"], []).append(row)
    return history

def save_entry(entry):
    write_header = not HISTORY_FILE.exists()
    with open(HISTORY_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if write_header:
            w.writeheader()
        w.writerow(entry)

def last_price(history, name):
    entries = history.get(name, [])
    if not entries:
        return None
    try:
        return float(entries[-1]["price"])
    except (ValueError, KeyError):
        return None

def price_history_list(history, name, n=7):
    entries = history.get(name, [])[-n:]
    return [(e["date"], float(e["price"])) for e in entries if e["price"] not in ("", "N/A")]

# ─── SCRAPER ─────────────────────────────────────────────────────────────────

def scrape_products(products):
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=500)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
        )
        page = context.new_page()

        log("Logging into SGProof...")
        try:
            page.goto("https://shop.sgproof.com/", timeout=30000)
            page.wait_for_timeout(3000)

            # Accept age gate if present
            try:
                yes_btn = page.locator("button:has-text('Yes'), a:has-text('Yes')").first
                if yes_btn.is_visible(timeout=3000):
                    yes_btn.click()
                    page.wait_for_timeout(2000)
            except Exception:
                pass

            # Click the Log In link to open the login popup
            try:
                page.locator("a:has-text('Log In'), button:has-text('Log In')").first.click()
                page.wait_for_timeout(3000)
            except Exception:
                pass

            # Wait for the gigya login form to appear and become visible
            email_sel = "input[data-gigya-name='email'], input[name='email'], input[type='email']"
            page.wait_for_selector(email_sel, state="visible", timeout=15000)

            # Fill email
            page.locator(email_sel).first.click()
            page.wait_for_timeout(500)
            page.locator(email_sel).first.fill(SGPROOF_EMAIL)
            page.wait_for_timeout(500)

            # Fill password
            pass_sel = "input[data-gigya-name='password'], input[name='password'], input[type='password']"
            page.locator(pass_sel).first.click()
            page.wait_for_timeout(500)
            page.locator(pass_sel).first.fill(SGPROOF_PASSWORD)
            page.wait_for_timeout(500)

            # Click submit button
            try:
                page.locator("input[type='submit'], button[type='submit'], .gigya-input-submit").first.click()
            except Exception:
                page.keyboard.press("Enter")

            page.wait_for_timeout(5000)
            log("Login submitted.")

        except Exception as e:
            log(f"ERROR during login: {e}")
            browser.close()
            return results

        today = datetime.now().strftime("%Y-%m-%d")

        for product in products:
            name = product["name"]
            url  = product.get("url", "")
            if not url:
                log(f"  SKIP: {name} — no URL")
                continue

            log(f"  Checking: {name}")
            try:
                page.goto(url, timeout=25000)
                page.wait_for_timeout(3000)

                price_raw = None
                for sel in [".product-price",".price","[class*='price']","[data-price]",".pdp-price",".unit-price"]:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=2000):
                            price_raw = el.inner_text().strip()
                            break
                    except Exception:
                        continue

                price_num = None
                if price_raw:
                    nums = re.findall(r"\d+\.?\d*", price_raw.replace(",",""))
                    if nums:
                        price_num = float(nums[0])

                deal_text = ""
                for sel in ["[class*='deal']","[class*='bundle']","[class*='promo']","[class*='promotion']","[class*='offer']","[class*='pack']"]:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=1500):
                            txt = el.inner_text().strip()
                            if txt:
                                deal_text = txt[:200]
                                break
                    except Exception:
                        continue

                unit_count = 1
                page_text  = page.inner_text("body")[:3000]
                pack_match = re.search(r"(\d+)\s*[-]?\s*(pack|bottle|unit|case|pk)\b", page_text, re.IGNORECASE)
                if pack_match:
                    unit_count = int(pack_match.group(1))

                per_unit = round(price_num / unit_count, 2) if price_num and unit_count > 1 else ""

                entry = {
                    "date":       today,
                    "name":       name,
                    "price":      price_num if price_num is not None else "N/A",
                    "deal_text":  deal_text,
                    "unit_count": unit_count,
                    "per_unit":   per_unit,
                    "category":   product.get("category",""),
                    "url":        url,
                }
                results.append(entry)

                price_str = f"${price_num:.2f}" if price_num else "N/A"
                pack_str  = f" ({unit_count}-pack, ${per_unit}/ea)" if unit_count > 1 else ""
                log(f"    Price: {price_str}{pack_str}  Deal: {deal_text[:60] or 'none'}")

            except Exception as e:
                log(f"    ERROR on {name}: {e}")

        browser.close()
    return results

# ─── DEAL DETECTION ──────────────────────────────────────────────────────────

def detect_deals(results, history):
    deals, stable = [], []
    for entry in results:
        name = entry["name"]
        try:
            current = float(entry["price"])
        except (ValueError, TypeError):
            continue

        prev    = last_price(history, name)
        reasons = []
        is_deal = False

        if prev is not None and current < prev:
            drop_pct = ((prev - current) / prev) * 100
            if drop_pct >= ALERT_THRESHOLD_PCT:
                reasons.append(f"Price dropped {drop_pct:.1f}% (was ${prev:.2f})")
                is_deal = True

        if prev is None:
            reasons.append(f"First time tracked — logged at ${current:.2f}")

        if entry.get("deal_text"):
            reasons.append(f"Promo on page: {entry['deal_text'][:80]}")
            is_deal = True

        if int(entry.get("unit_count", 1)) >= 3:
            reasons.append(f"{entry['unit_count']}-pack @ ${current:.2f} = ${entry['per_unit']:.2f}/bottle")
            is_deal = True

        rec = {**entry, "prev": prev, "reasons": reasons, "is_deal": is_deal,
               "history": price_history_list(history, name)}

        if is_deal or reasons:
            deals.append(rec)
        else:
            stable.append(rec)

    return deals, stable

# ─── HTML REPORT ─────────────────────────────────────────────────────────────

def sparkline_svg(history_points, current):
    if len(history_points) < 2:
        return ""
    prices = [p for _, p in history_points] + [current]
    mn, mx = min(prices), max(prices)
    rng    = mx - mn or 1
    w, h   = 80, 28
    pts    = []
    for i, price in enumerate(prices):
        x = int(i / (len(prices)-1) * w)
        y = int(h - ((price - mn) / rng) * h)
        pts.append(f"{x},{y}")
    polyline = " ".join(pts)
    last_x, last_y = pts[-1].split(",")
    color = "#1D9E75" if current <= (history_points[-1][1] if history_points else current) else "#E24B4A"
    return f'''<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block">
      <polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="1.5" stroke-linejoin="round"/>
      <circle cx="{last_x}" cy="{last_y}" r="2.5" fill="{color}"/>
    </svg>'''

def write_html(deals, stable, run_time):
    today     = datetime.now().strftime("%B %d, %Y")
    deal_count = len(deals)

    def product_card(d, highlight):
        prev      = d.get("prev")
        current   = d["price"] if isinstance(d["price"], float) else None
        hist      = d.get("history", [])
        spark     = sparkline_svg(hist, current) if current else ""
        change_html = ""
        if prev and current:
            diff  = current - prev
            sign  = "+" if diff >= 0 else ""
            color = "#1D9E75" if diff < 0 else "#E24B4A"
            change_html = f'<span style="color:{color};font-size:12px;"> ({sign}${diff:.2f})</span>'

        reasons_html = "".join(
            f'<li style="margin:2px 0;font-size:13px;color:#555;">{r}</li>'
            for r in d.get("reasons", [])
        ) if d.get("reasons") else '<li style="font-size:13px;color:#aaa;">Price stable</li>'

        border = "2px solid #1D9E75" if highlight else "1px solid #e8e8e8"
        badge  = '<span style="background:#E1F5EE;color:#0F6E56;font-size:11px;font-weight:600;padding:2px 8px;border-radius:4px;margin-left:8px;">DEAL</span>' if highlight else ""

        price_str = f"${current:.2f}" if current else "N/A"
        pack_str  = ""
        if int(d.get("unit_count",1)) > 1:
            pack_str = f'<div style="font-size:12px;color:#1D9E75;margin-top:2px;">{d["unit_count"]}-pack · ${d["per_unit"]:.2f}/bottle</div>'

        return f'''
        <div style="background:#fff;border:{border};border-radius:10px;padding:14px 16px;margin-bottom:10px;">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;">
            <div style="flex:1;">
              <div style="font-weight:600;font-size:14px;color:#1a1a1a;">{d["name"]}{badge}</div>
              <div style="font-size:12px;color:#888;margin-top:2px;">{d.get("category","")}</div>
              <ul style="margin:8px 0 0;padding-left:16px;">{reasons_html}</ul>
            </div>
            <div style="text-align:right;flex-shrink:0;">
              <div style="font-size:22px;font-weight:700;color:#1a1a1a;">{price_str}{change_html}</div>
              {pack_str}
              <div style="margin-top:6px;">{spark}</div>
              <a href="{d.get("url","https://shop.sgproof.com")}" target="_blank"
                 style="display:inline-block;margin-top:8px;font-size:12px;color:#185FA5;text-decoration:none;">
                View on SGProof →
              </a>
            </div>
          </div>
        </div>'''

    deals_html  = "".join(product_card(d, True)  for d in deals)  or '<p style="color:#aaa;font-size:14px;">No deals found today.</p>'
    stable_html = "".join(product_card(d, False) for d in stable) or ""

    stable_section = f'''
      <details style="margin-top:24px;">
        <summary style="cursor:pointer;font-size:14px;color:#888;user-select:none;">
          Show {len(stable)} stable product{'s' if len(stable)!=1 else ''} (no deal today)
        </summary>
        <div style="margin-top:12px;">{stable_html}</div>
      </details>''' if stable else ""

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SGProof Deal Report — {today}</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#f5f5f0; color:#1a1a1a; }}
  .wrap {{ max-width:680px; margin:0 auto; padding:24px 16px; }}
  h1 {{ font-size:22px; font-weight:700; }}
  .sub {{ font-size:13px; color:#888; margin-top:4px; }}
  .stat {{ display:inline-block; background:#fff; border:1px solid #e8e8e8; border-radius:8px;
           padding:10px 18px; margin:16px 8px 16px 0; text-align:center; }}
  .stat-num {{ font-size:24px; font-weight:700; }}
  .stat-lbl {{ font-size:12px; color:#888; margin-top:2px; }}
  .green {{ color:#1D9E75; }}
  .section-title {{ font-size:13px; font-weight:600; color:#555; text-transform:uppercase;
                    letter-spacing:.05em; margin:20px 0 10px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>SGProof Deal Report</h1>
  <div class="sub">Run on {today} at {run_time} · <a href="https://shop.sgproof.com" style="color:#185FA5;">Open SGProof</a></div>

  <div style="margin:16px 0;">
    <div class="stat">
      <div class="stat-num green">{deal_count}</div>
      <div class="stat-lbl">Deals found</div>
    </div>
    <div class="stat">
      <div class="stat-num">{len(deals)+len(stable)}</div>
      <div class="stat-lbl">Products checked</div>
    </div>
  </div>

  <div class="section-title">{"Deals — good time to buy" if deal_count else "Today's prices"}</div>
  {deals_html}
  {stable_section}

  <div style="margin-top:32px;padding-top:16px;border-top:1px solid #e8e8e8;font-size:12px;color:#bbb;">
    Generated by SGProof Deal Tracker · price_history.csv has your full log
  </div>
</div>
</body>
</html>'''

    DEALS_HTML_FILE.write_text(html, encoding="utf-8")
    log(f"Report saved → {DEALS_HTML_FILE.resolve()}")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    log("="*50)
    log("SGProof Deal Tracker starting")

    if not SGPROOF_EMAIL or not SGPROOF_PASSWORD:
        log("ERROR: Set SGPROOF_EMAIL and SGPROOF_PASSWORD in your .env file")
        sys.exit(1)

    products          = load_products()
    history           = load_history()
    results           = scrape_products(products)

    if not results:
        log("No results — check login and URLs in products.json")
        return

    for entry in results:
        save_entry(entry)

    deals, stable     = detect_deals(results, history)
    run_time          = datetime.now().strftime("%I:%M %p")

    write_html(deals, stable, run_time)

    log(f"Deals found: {len(deals)} | Stable: {len(stable)}")
    log(f"Open deals_today.html in your browser to see results.")

if __name__ == "__main__":
    main()

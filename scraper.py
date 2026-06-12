"""
SGProof Deal Scraper
====================
1. Opens browser — you log in manually
2. Scrapes the Deals page for all products
3. For each product, reads the deal pricing table
4. Filters for deals with minimum quantity less than a full case (under 12 units)
5. If no deals page found, checks individual product pages
6. Saves deals_today.html grouped by deal tier

Run: python3 scraper.py
"""

from playwright.sync_api import sync_playwright
from pathlib import Path
from datetime import datetime
import re, json

DEALS_HTML = Path("deals_today.html")
HISTORY    = Path("price_history.json")

# Anything less than this is considered a small bundle (not a full case commitment)
CASE_SIZE  = 12

def load_history():
    if HISTORY.exists():
        return json.loads(HISTORY.read_text())
    return {}

def save_history(data):
    HISTORY.write_text(json.dumps(data, indent=2))

def parse_price(text):
    if not text: return None
    nums = re.findall(r"\$?([\d,]+\.?\d*)", str(text).replace(",",""))
    try: return float(nums[0]) if nums else None
    except: return None

def parse_qty(text):
    """Extract unit count from text like '3 Case', '5 Units', '6 Bottles'"""
    if not text: return None
    text = str(text).strip().lower()
    # e.g. "1 case" -> 12 units, "3 case" -> 36 units
    case_match = re.search(r"(\d+)\s*case", text)
    if case_match:
        return int(case_match.group(1)) * CASE_SIZE
    # e.g. "3 units", "6 bottles"
    unit_match = re.search(r"(\d+)\s*(unit|bottle|btl|pk|pack)", text)
    if unit_match:
        return int(unit_match.group(1))
    # bare number
    bare = re.search(r"^(\d+)$", text.strip())
    if bare:
        return int(bare.group(1))
    return None

def parse_deal_table(card_or_page):
    """
    Reads SGProof's deal pricing table from a product card or page.
    Returns list of tiers: [{min_qty, min_cases, discount, price_per_case, price_per_unit, price_per_oz}]
    """
    tiers = []
    try:
        # Look for table rows containing deal info
        rows = card_or_page.locator("table tr, [class*='deal'] tr, [class*='price-tier'] tr, [class*='tier'] tr").all()
        for row in rows:
            cells = [c.inner_text().strip() for c in row.locator("td, th").all()]
            if len(cells) < 3:
                continue
            text = " ".join(cells).lower()
            # Skip header rows
            if "minimum" in text and "discount" in text:
                continue
            # Try to parse qty, discount, prices from cells
            qty      = None
            discount = None
            p_case   = None
            p_unit   = None
            p_oz     = None
            for cell in cells:
                c = cell.strip()
                if not qty and re.search(r'\d+\s*(case|unit|bottle|btl|pack)', c, re.I):
                    qty = parse_qty(c)
                elif not qty and re.match(r'^\d+$', c):
                    qty = int(c) * CASE_SIZE  # bare number = cases
                elif not discount and re.search(r'\$[\d.]+', c) and not p_case:
                    discount = parse_price(c)
                elif not p_case and parse_price(c) and parse_price(c) > 50:
                    p_case = parse_price(c)
                elif not p_unit and parse_price(c) and parse_price(c) < 100:
                    p_unit = parse_price(c)
                elif not p_oz and parse_price(c) and parse_price(c) < 10:
                    p_oz = parse_price(c)

            if qty is not None:
                tiers.append({
                    "min_qty_units": qty,
                    "min_cases":     round(qty / CASE_SIZE, 1),
                    "discount":      discount,
                    "price_per_case":p_case,
                    "price_per_unit":p_unit,
                    "price_per_oz":  p_oz,
                })
    except Exception as e:
        pass
    return tiers

def scrape_deal_page(page):
    """Go to deals/promotions pages and collect all product links + deal info."""
    products = []
    seen_urls = set()

    deal_pages = [
        "https://shop.sgproof.com/sgws/en/usd/deals",
        "https://shop.sgproof.com/sgws/en/usd/promotions",
        "https://shop.sgproof.com/sgws/en/usd/specials",
        "https://shop.sgproof.com/sgws/en/usd/search?q=deal",
    ]

    for url in deal_pages:
        print(f"  Checking: {url}")
        try:
            page.goto(url, timeout=25000)
            page.wait_for_timeout(3000)

            # Scroll to load everything
            for _ in range(4):
                page.keyboard.press("End")
                page.wait_for_timeout(800)

            # Find product cards
            card_sels = [
                ".product-card","[class*='product-card']",
                ".product-item","[class*='product-item']",
                ".product-tile","[class*='product-tile']",
                ".cx-product-card",".product",
            ]
            cards = []
            for sel in card_sels:
                found = page.locator(sel).all()
                if len(found) > 0:
                    cards = found
                    break

            print(f"    {len(cards)} products found")

            for card in cards:
                try:
                    # Get name
                    name = None
                    for ns in ["h2","h3","h4","[class*='name']","[class*='title']","a"]:
                        try:
                            el = card.locator(ns).first
                            if el.is_visible(timeout=400):
                                t = el.inner_text().strip()
                                if t and len(t) > 3:
                                    name = t[:100]
                                    break
                        except: pass
                    if not name:
                        name = card.inner_text().strip().split("\n")[0][:80]

                    # Get product URL
                    prod_url = None
                    try:
                        href = card.locator("a").first.get_attribute("href")
                        if href:
                            prod_url = href if href.startswith("http") else "https://shop.sgproof.com"+href
                    except: pass

                    if prod_url and prod_url in seen_urls:
                        continue
                    if prod_url:
                        seen_urls.add(prod_url)

                    # Try to read deal table directly from card
                    tiers = parse_deal_table(card)

                    # Get base price
                    card_text  = card.inner_text()
                    all_prices = [parse_price(p) for p in re.findall(r"\$[\d,]+\.?\d*", card_text)]
                    all_prices = sorted(set(p for p in all_prices if p and p > 0))
                    base_price = all_prices[-1] if all_prices else None

                    products.append({
                        "name":       name,
                        "url":        prod_url or url,
                        "base_price": base_price,
                        "tiers":      tiers,
                        "source":     "Deals page",
                    })
                except: continue

        except Exception as e:
            print(f"    Could not load: {e}")

    return products, seen_urls

def scrape_individual_pages(page, products, seen_urls):
    """
    For products that have no deal tiers yet, click into the product page
    and read the deal table there.
    Also finds any deal tables on product pages not caught by the deals page.
    """
    enriched = []
    for prod in products:
        if prod["tiers"] or not prod.get("url"):
            enriched.append(prod)
            continue
        print(f"  Checking product page: {prod['name'][:50]}...")
        try:
            page.goto(prod["url"], timeout=20000)
            page.wait_for_timeout(2500)
            tiers = parse_deal_table(page)
            prod["tiers"] = tiers
            if tiers:
                print(f"    Found {len(tiers)} deal tiers")
        except Exception as e:
            print(f"    Error: {e}")
        enriched.append(prod)
    return enriched

def filter_small_bundles(products):
    """
    Keep only products that have at least one tier
    with min_qty_units < CASE_SIZE (i.e. less than a full case).
    """
    result = []
    for prod in products:
        small_tiers = [t for t in prod.get("tiers",[]) if t["min_qty_units"] < CASE_SIZE]
        if small_tiers:
            prod["small_tiers"] = small_tiers
            result.append(prod)
    return result

def write_html(products, history):
    today    = datetime.now().strftime("%B %d, %Y")
    run_time = datetime.now().strftime("%I:%M %p")

    def tier_rows(tiers):
        rows = ""
        for t in tiers:
            qty_label = f"{t['min_qty_units']} units"
            if t['min_qty_units'] % CASE_SIZE == 0:
                qty_label = f"{int(t['min_qty_units']/CASE_SIZE)} case"
            disc  = f"${t['discount']:.2f}"  if t.get("discount")  else "—"
            pcase = f"${t['price_per_case']:.2f}" if t.get("price_per_case") else "—"
            punit = f"${t['price_per_unit']:.2f}" if t.get("price_per_unit") else "—"
            poz   = f"${t['price_per_oz']:.3f}"   if t.get("price_per_oz")   else "—"
            rows += f"""<tr>
              <td style="padding:6px 10px;font-size:13px;color:#1a1a1a">{qty_label}</td>
              <td style="padding:6px 10px;font-size:13px;color:#1D9E75;font-weight:500">{disc}</td>
              <td style="padding:6px 10px;font-size:13px">{pcase}</td>
              <td style="padding:6px 10px;font-size:13px;font-weight:600;color:#1a1a1a">{punit}</td>
              <td style="padding:6px 10px;font-size:13px;color:#888">{poz}</td>
            </tr>"""
        return rows

    def product_card(prod):
        hist     = history.get(prod["name"], [])
        trend    = ""
        if len(hist) >= 2:
            prev = hist[-2].get("best_unit_price")
            best = min((t["price_per_unit"] for t in prod.get("small_tiers",[]) if t.get("price_per_unit")), default=None)
            if prev and best:
                if best < prev:   trend = '<span style="color:#1D9E75;font-size:11px"> ↓ cheaper than last time</span>'
                elif best > prev: trend = '<span style="color:#E24B4A;font-size:11px"> ↑ higher than last time</span>'
                else:             trend = '<span style="color:#888;font-size:11px"> = same as last time</span>'

        best_unit = min((t["price_per_unit"] for t in prod.get("small_tiers",[]) if t.get("price_per_unit")), default=None)
        best_str  = f'<span style="color:#1D9E75;font-size:13px;font-weight:600"> · Best: ${best_unit:.2f}/unit</span>' if best_unit else ""

        rows = tier_rows(prod.get("small_tiers", prod.get("tiers",[])))
        table = f"""<table style="width:100%;border-collapse:collapse;margin-top:10px">
          <thead><tr style="background:#f9f9f7">
            <th style="padding:6px 10px;font-size:11px;color:#888;text-align:left;font-weight:500">Min qty</th>
            <th style="padding:6px 10px;font-size:11px;color:#888;text-align:left;font-weight:500">Discount</th>
            <th style="padding:6px 10px;font-size:11px;color:#888;text-align:left;font-weight:500">Price/case</th>
            <th style="padding:6px 10px;font-size:11px;color:#888;text-align:left;font-weight:500">Price/unit</th>
            <th style="padding:6px 10px;font-size:11px;color:#888;text-align:left;font-weight:500">Price/oz</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>""" if rows else "<p style='font-size:13px;color:#aaa;margin-top:8px'>No tier table found — check product page.</p>"

        return f"""<div style="background:#fff;border:1px solid #eee;border-radius:10px;padding:14px 16px;margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div style="flex:1;min-width:0">
              <div style="font-weight:600;font-size:14px;color:#1a1a1a">{prod['name']}{trend}</div>
              <div style="font-size:12px;color:#888;margin-top:2px">{prod['source']}{best_str}</div>
            </div>
            <a href="{prod['url']}" target="_blank" style="font-size:12px;color:#185FA5;text-decoration:none;flex-shrink:0;margin-left:12px">View on SGProof →</a>
          </div>
          {table}
        </div>"""

    cards_html = "".join(product_card(p) for p in products)
    total = len(products)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SGProof Small Bundle Deals — {today}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f5f0;color:#1a1a1a}}
  .wrap{{max-width:760px;margin:0 auto;padding:24px 16px}}
  .stat{{display:inline-block;background:#fff;border:1px solid #eee;border-radius:8px;padding:10px 18px;margin:0 8px 12px 0;text-align:center}}
  .stat-num{{font-size:22px;font-weight:700}}
  .stat-lbl{{font-size:12px;color:#888;margin-top:2px}}
</style>
</head>
<body>
<div class="wrap">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px">
    <div>
      <h1 style="font-size:22px;font-weight:700">SGProof small bundle deals</h1>
      <div style="font-size:13px;color:#888">Deals under 1 full case · {today} at {run_time}</div>
    </div>
    <a href="https://shop.sgproof.com" target="_blank" style="font-size:13px;color:#185FA5;text-decoration:none">Open SGProof →</a>
  </div>

  <div style="margin-bottom:20px">
    <div class="stat"><div class="stat-num" style="color:#1D9E75">{total}</div><div class="stat-lbl">Products with small bundle deals</div></div>
  </div>

  {cards_html if cards_html else '<p style="color:#888;font-size:14px;padding:2rem 0;text-align:center">No small bundle deals found today.</p>'}

  <div style="margin-top:32px;padding-top:16px;border-top:1px solid #e8e8e8;font-size:12px;color:#bbb">
    Only showing deals where minimum quantity is less than one full case (12 units)
  </div>
</div>
</body>
</html>"""

    DEALS_HTML.write_text(html, encoding="utf-8")
    print(f"\nReport saved → {DEALS_HTML.resolve()}")

def main():
    print("="*50)
    print("SGProof Small Bundle Deal Scraper")
    print("="*50)

    history = load_history()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            viewport={"width":1280,"height":800}
        )
        page = context.new_page()

        print("\nOpening SGProof...")
        page.goto("https://shop.sgproof.com/", timeout=40000)

        print("\n" + "="*50)
        print("ACTION NEEDED in the browser window:")
        print("  1. Select Texas (TX)")
        print("  2. Click Yes for 21+ age gate")
        print("  3. Log in with your credentials")
        print("  4. Wait until you see the main product page")
        print("="*50)
        input("\nPress Enter here once you are fully logged in...")

        print("\nStep 1 — Scraping deals page...")
        products, seen_urls = scrape_deal_page(page)
        print(f"  Found {len(products)} products on deals pages")

        print("\nStep 2 — Checking individual product pages for deal tables...")
        products = scrape_individual_pages(page, products, seen_urls)

        browser.close()

    print("\nStep 3 — Filtering for small bundle deals (under 1 case)...")
    small_bundle_products = filter_small_bundles(products)
    print(f"  {len(small_bundle_products)} products have small bundle deals")

    # Save history
    today = datetime.now().strftime("%Y-%m-%d")
    for prod in small_bundle_products:
        best_unit = min(
            (t["price_per_unit"] for t in prod.get("small_tiers",[]) if t.get("price_per_unit")),
            default=None
        )
        history.setdefault(prod["name"], []).append({
            "date": today,
            "best_unit_price": best_unit,
            "tiers": prod.get("small_tiers",[])
        })
    save_history(history)

    write_html(small_bundle_products, history)
    print("\nDone! Run this to open the report:")
    print("  open deals_today.html")

if __name__ == "__main__":
    main()

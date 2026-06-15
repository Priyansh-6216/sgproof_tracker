"""
SGProof Deal Tracker
====================
Logs into shop.sgproof.com (automates age gate, TX market select, and login),
checks prices on your tracked products (loaded from products.csv),
saves history to CSV, and writes deals_today.html with a sorted table of best purchasing recommendations.

Setup:
  1. pip install playwright python-dotenv
  2. playwright install chromium
  3. Copy .env.example to .env and fill in your SGProof login
  4. Edit products.csv with your items
  5. Run: python tracker.py
"""

import json, csv, os, sys, re
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Bulletproof fallback: manually parse .env if python-dotenv is not installed
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

# ─── CONFIG ──────────────────────────────────────────────────────────────────

SGPROOF_EMAIL    = os.getenv("SGPROOF_EMAIL", "")
SGPROOF_PASSWORD = os.getenv("SGPROOF_PASSWORD", "")

PRODUCTS_CSV_FILE  = Path("products.csv")
PRODUCTS_JSON_FILE = Path("products.json")
HISTORY_FILE       = Path("price_history.csv")
DEALS_HTML_FILE    = Path("deals_today.html")
LOG_FILE           = Path("tracker.log")

ALERT_THRESHOLD_PCT = 3.0   # alert if price drops this % or more

# ─── LOGGING ─────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ─── PRODUCTS ────────────────────────────────────────────────────────────────

def load_products():
    """
    Loads products from products.csv. Fallback to products.json if CSV is missing.
    If neither exists, creates a default products.csv template.
    """
    if PRODUCTS_CSV_FILE.exists():
        log(f"Loading products from {PRODUCTS_CSV_FILE}")
        products = []
        try:
            with open(PRODUCTS_CSV_FILE, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Strip spaces from keys and values
                    item = {k.strip(): v.strip() for k, v in row.items() if k}
                    if "name" in item and item["name"]:
                        products.append(item)
            return products
        except Exception as e:
            log(f"Error reading {PRODUCTS_CSV_FILE}: {e}")

    if PRODUCTS_JSON_FILE.exists():
        log(f"Loading products from legacy {PRODUCTS_JSON_FILE}")
        try:
            return json.loads(PRODUCTS_JSON_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"Error reading {PRODUCTS_JSON_FILE}: {e}")

    log("No product config found — creating example products.csv")
    example_headers = ["name", "url", "category", "case_size"]
    example_rows = [
        {"name": "Tito's Handmade Vodka 1.75L", "url": "", "category": "Vodka", "case_size": "6"},
        {"name": "Patron Silver Tequila 750ml", "url": "", "category": "Tequila", "case_size": "12"},
        {"name": "Hennessy VS Cognac 750ml", "url": "", "category": "Cognac", "case_size": "12"},
        {"name": "Jack Daniel's Tennessee Whiskey 1L", "url": "", "category": "Whiskey", "case_size": "12"},
        {"name": "Don Julio Blanco 750ml", "url": "", "category": "Tequila", "case_size": "12"},
    ]
    try:
        with open(PRODUCTS_CSV_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=example_headers)
            w.writeheader()
            w.writerows(example_rows)
        log("Example products.csv created. Please edit it and run again.")
    except Exception as e:
        log(f"Could not create example products.csv: {e}")
        
    return example_rows

# ─── HISTORY ─────────────────────────────────────────────────────────────────

HISTORY_FIELDS = ["date", "name", "price", "case_size", "best_price", "best_qty", "recommendation", "category", "url"]

def load_history():
    history = {}
    if not HISTORY_FILE.exists():
        return history
    try:
        with open(HISTORY_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                history.setdefault(row["name"], []).append(row)
    except Exception as e:
        log(f"Error loading history: {e}")
    return history

def save_entry(entry):
    write_header = not HISTORY_FILE.exists()
    try:
        with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
            if write_header:
                w.writeheader()
            w.writerow({k: entry.get(k, "") for k in HISTORY_FIELDS})
    except Exception as e:
        log(f"Error saving history entry: {e}")

def last_price(history, name):
    entries = history.get(name, [])
    if not entries:
        return None
    try:
        return float(entries[-1]["best_price"])
    except (ValueError, KeyError, TypeError):
        return None

def price_history_list(history, name, n=7):
    entries = history.get(name, [])[-n:]
    points = []
    for e in entries:
        try:
            val = e.get("best_price", "")
            if val not in ("", "N/A"):
                points.append((e["date"], float(val)))
        except ValueError:
            continue
    return points

# ─── GATES & SETUP HELPERS ───────────────────────────────────────────────────

def select_texas(page):
    """
    Attempts to select Texas state from the state/market overlay popup.
    """
    log("Attempting to select Texas state market...")
    try:
        # Check standard select elements first
        selects = page.locator("select").all()
        for s in selects:
            if s.is_visible(timeout=1500):
                for label in ["Texas", "TX", "texas", "tx"]:
                    try:
                        s.select_option(label=label)
                        log(f"  Selected label '{label}' in dropdown.")
                        return True
                    except Exception:
                        try:
                            s.select_option(value=label)
                            log(f"  Selected value '{label}' in dropdown.")
                            return True
                        except Exception:
                            pass
    except Exception as e:
        log(f"  Dropdown selection error: {e}")

    # Look for button or list elements containing Texas/TX
    for sel_type in ["button", "a", "span", "div", "li", "option"]:
        try:
            loc = page.locator(f"{sel_type}:has-text('Texas'), {sel_type}:has-text('TX')").first
            if loc.is_visible(timeout=1500):
                loc.click()
                log(f"  Clicked {sel_type} containing Texas/TX.")
                
                # Check for confirm buttons
                confirm = page.locator("button:has-text('Confirm'), button:has-text('Save'), button:has-text('Submit'), button:has-text('Select')").first
                if confirm.is_visible(timeout=1500):
                    confirm.click()
                    log("  Clicked market confirmation button.")
                return True
        except Exception:
            continue
            
    log("  Texas market selector not found or already bypassed.")
    return False

def dismiss_overlays(page):
    """
    Finds and dismisses common overlay popups, cookie warnings, and background modals.
    """
    log("Checking for popups, overlays, and cookie sheets...")
    # Cookie selectors
    cookie_sels = ["button:has-text('Accept'), button:has-text('Agree'), button:has-text('Allow'), button:has-text('Cookie')"]
    for sel in cookie_sels:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=1500):
                el.click()
                log(f"  Dismissed cookie dialog using: {sel}")
                page.wait_for_timeout(1000)
        except Exception:
            pass

    # Generic close buttons
    close_sels = [
        "button[aria-label='Close']", ".modal-close", "[class*='close']", 
        "button:has-text('Close')", "a:has-text('Close')", ".dialog-close"
    ]
    for sel in close_sels:
        try:
            els = page.locator(sel).all()
            for el in els:
                # Click only if it is visible and does not contain Login text to avoid closing critical flows
                if el.is_visible(timeout=500) and not el.locator("text=Log In").is_visible():
                    el.click()
                    log(f"  Closed popup overlay using selector: {sel}")
                    page.wait_for_timeout(1000)
        except Exception:
            pass

# ─── PARSING HELPERS ──────────────────────────────────────────────────────────

def parse_price(text):
    if not text:
        return None
    nums = re.findall(r"\$?([\d,]+\.?\d*)", str(text).replace(",", ""))
    try:
        return float(nums[0]) if nums else None
    except Exception:
        return None

def parse_qty(text, case_size):
    """Extract unit count from text like '3 Case', '5 Units', '6 Bottles'"""
    if not text:
        return None
    text = str(text).strip().lower()
    case_match = re.search(r"(\d+)\s*case", text)
    if case_match:
        return int(case_match.group(1)) * case_size
    unit_match = re.search(r"(\d+)\s*(unit|bottle|btl|pk|pack)", text)
    if unit_match:
        return int(unit_match.group(1))
    bare = re.search(r"^(\d+)$", text.strip())
    if bare:
        return int(bare.group(1))
    return None

def scrape_deal_table(page, case_size):
    """
    Parses the deal pricing table on the product details page.
    Returns: [{min_qty_units, min_cases, discount, price_per_case, price_per_unit, price_per_oz}]
    """
    tiers = []
    try:
        # Locate potential table rows
        rows = page.locator("table tr, [class*='deal'] tr, [class*='price-tier'] tr, [class*='tier'] tr").all()
        for row in rows:
            cells = [c.inner_text().strip() for c in row.locator("td, th").all()]
            if len(cells) < 2:
                continue
            text = " ".join(cells).lower()
            # Skip header rows
            if "minimum" in text and "discount" in text:
                continue
            
            qty = None
            discount = None
            p_case = None
            p_unit = None
            p_oz = None
            
            for cell in cells:
                c = cell.strip()
                if not qty and re.search(r'\d+\s*(case|unit|bottle|btl|pack)', c, re.I):
                    qty = parse_qty(c, case_size)
                elif not qty and re.match(r'^\d+$', c):
                    qty = int(c) * case_size
                elif not discount and re.search(r'\$[\d.]+', c) and not p_case:
                    discount = parse_price(c)
                elif not p_case and parse_price(c) and parse_price(c) > 50:
                    p_case = parse_price(c)
                elif not p_unit and parse_price(c) and parse_price(c) < 100:
                    p_unit = parse_price(c)
                elif not p_oz and parse_price(c) and parse_price(c) < 10:
                    p_oz = parse_price(c)
            
            if qty is not None:
                # Fill missing calculated prices
                if not p_unit and p_case:
                    p_unit = round(p_case / case_size, 2)
                if not p_case and p_unit:
                    p_case = round(p_unit * case_size, 2)
                
                tiers.append({
                    "min_qty_units": qty,
                    "min_cases": round(qty / case_size, 1),
                    "discount": discount,
                    "price_per_case": p_case,
                    "price_per_unit": p_unit,
                    "price_per_oz": p_oz,
                })
    except Exception as e:
        log(f"    Error parsing deal table: {e}")
    return tiers

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

        log("Opening SGProof homepage...")
        try:
            page.goto("https://shop.sgproof.com/", timeout=45000)
            page.wait_for_timeout(3000)

            # 1. Bypass age gate & select Texas state
            try:
                log("Locating age gate...")
                modal = page.locator("#ageGatemodal")
                if modal.is_visible(timeout=8000):
                    state_select = modal.locator("select").first
                    if state_select.is_visible():
                        try:
                            state_select.select_option("TX")
                        except Exception:
                            # Try by label if value fails
                            state_select.select_option(label="Texas")
                        log("  Selected Texas in modal.")
                        page.wait_for_timeout(1000)
                    
                    yes_btn = modal.locator("button:has-text('Yes')").first
                    if not yes_btn.is_visible():
                        yes_btn = modal.get_by_role("button", name="Yes").first
                    if yes_btn.is_visible():
                        yes_btn.click()
                        log("  Clicked Yes in modal.")
                    page.wait_for_timeout(3000)
                else:
                    # Fallback to iframe if it's there
                    frame = page.frame_locator("#mock-iframe")
                    state_select = frame.locator("select")
                    if state_select.is_visible(timeout=5000):
                        state_select.select_option("TX")
                        log("  Selected Texas (TX) inside iframe.")
                        page.wait_for_timeout(1000)
                        yes_btn = frame.get_by_role("button", name="Yes").first
                        yes_btn.click()
                        log("  Clicked Yes inside iframe age gate.")
                        page.wait_for_timeout(3000)
            except Exception as e:
                log(f"  Age gate / Texas selection bypass failed: {e}")

            # 2. Click the Log In button in the homepage header
            try:
                login_btn = page.get_by_role("button", name="Log In").first
                login_btn.wait_for(state="visible", timeout=15000)
                login_btn.click()
                log("  Clicked header Log In button.")
                page.wait_for_timeout(3000)
            except Exception as e:
                log(f"  Failed to click header Log In button: {e}")

            # 3. Fill credentials and log in
            try:
                email_input = page.locator("#email")
                email_input.wait_for(state="visible", timeout=15000)
                email_input.fill(SGPROOF_EMAIL)
                page.wait_for_timeout(500)
                
                pass_input = page.locator("#password")
                pass_input.fill(SGPROOF_PASSWORD)
                page.wait_for_timeout(500)
                
                submit_btn = page.get_by_role("button", name="Log in", exact=True).first
                submit_btn.click()
                log("  Submitted login form.")
                page.wait_for_timeout(6000)
            except Exception as e:
                log(f"  Error filling/submitting credentials: {e}")

            # 4. Close promotional popup modal if it appears
            try:
                close_btn = page.get_by_role("button", name="Close").first
                if close_btn.is_visible(timeout=3000):
                    close_btn.click()
                    log("  Closed promotional popup modal.")
                    page.wait_for_timeout(1000)
            except Exception:
                pass

        except Exception as e:
            log(f"ERROR during setup and login: {e}")
            browser.close()
            return results

        today = datetime.now().strftime("%Y-%m-%d")

        for product in products:
            name = product["name"]
            url  = product.get("url", "")
            
            try:
                case_size = int(product.get("case_size", 12))
            except (ValueError, TypeError):
                case_size = 12

            log(f"  Searching for product: {name}")
            try:
                # Check if direct product page is configured in CSV (not a search URL)
                is_direct_pdp = url and ("shop.sgproof.com" in url) and ("/search" not in url)
                
                if is_direct_pdp:
                    log(f"    Direct PDP URL configured. Navigating directly: {url}")
                    page.goto(url, timeout=25000)
                else:
                    # Homepage Search Flow (Line-by-line Search)
                    log("    Navigating to homepage to search...")
                    page.goto("https://shop.sgproof.com/", timeout=25000)
                    page.wait_for_timeout(2500)

                    # Look for search input box
                    search_input = None
                    for sel in ["input.MuiAutocomplete-input", "input[role='combobox']", "input[type='search']", "input[placeholder*='Search']", "input[name='q']", "input[id*='search']"]:
                        try:
                            # Use locator.nth(0) and wait for it to be visible
                            el = page.locator(sel).first
                            el.wait_for(state="visible", timeout=8000)
                            if el.is_visible():
                                search_input = el
                                break
                        except Exception:
                            continue

                    if not search_input:
                        log(f"    ERROR: Could not locate homepage search box. Skipping {name}.")
                        continue

                    # Type product name and search
                    try:
                        search_input.click(force=True, timeout=3000)
                        page.wait_for_timeout(500)
                    except Exception:
                        pass
                    search_input.fill(name, force=True)
                    page.wait_for_timeout(500)
                    page.keyboard.press("Enter")
                    
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                        
                    # Hard wait to ensure the site actually loads the product grid
                    log("    Waiting for search results to load...")
                    page.wait_for_timeout(8000)

                    # Click first search result to access PDP
                    detail_link = None
                    
                    try:
                        # Grab all anchor tags that contain '/p/' (product links)
                        links = page.locator("a[href*='/p/']").all()
                        for link in links[:10]: # Check the first 10 links
                            if link.is_visible(timeout=1000):
                                href = link.get_attribute("href")
                                if href and "/p/" in href:
                                    detail_link = href
                                    break
                    except Exception as e:
                        log(f"    Failed grabbing product links: {e}")

                    if not detail_link:
                        # Fallback generic query
                        for sel in [".product-card a", ".product-item a", ".product-tile a"]:
                            try:
                                el = page.locator(sel).first
                                if el.is_visible(timeout=3000):
                                    detail_link = el.get_attribute("href")
                                    if detail_link:
                                        break
                            except Exception:
                                continue

                    if detail_link:
                        target_url = detail_link if detail_link.startswith("http") else f"https://shop.sgproof.com{detail_link}"
                        log(f"    Clicking through to PDP: {target_url}")
                        page.goto(target_url, timeout=25000)
                    else:
                        log(f"    WARNING: No search results found for: {name}")
                        continue

                # Wait and scroll down PDP page to trigger lazy loaded pricing table
                page.wait_for_timeout(2500)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
                page.wait_for_timeout(1000)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight * 2 / 3)")
                page.wait_for_timeout(1000)

                # Read standard unit price
                price_raw = None
                for sel in [".product-price", ".price", "[class*='price']", "[data-price]", ".pdp-price", ".unit-price"]:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=2000):
                            price_raw = el.inner_text().strip()
                            break
                    except Exception:
                        continue

                price_num = None
                if price_raw:
                    nums = re.findall(r"\d+\.?\d*", price_raw.replace(",", ""))
                    if nums:
                        price_num = float(nums[0])

                # Scrape deal table
                scraped_tiers = scrape_deal_table(page, case_size)

                # Extract promo labels
                deal_text = ""
                for sel in ["[class*='deal']", "[class*='bundle']", "[class*='promo']", "[class*='promotion']", "[class*='offer']", "[class*='pack']"]:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=1500):
                            txt = el.inner_text().strip()
                            if txt:
                                deal_text = txt[:200]
                                break
                    except Exception:
                        continue

                # Consolidate standard and bulk tier pricing
                all_tiers = []
                if price_num:
                    all_tiers.append({
                        "min_qty_units": 1,
                        "min_cases": round(1 / case_size, 2),
                        "discount": 0.0,
                        "price_per_case": round(price_num * case_size, 2),
                        "price_per_unit": price_num,
                        "price_per_oz": None,
                        "is_base": True
                    })
                all_tiers.extend(scraped_tiers)

                # Evaluate recommendations (find cheapest per-unit option)
                best_tier = None
                min_unit_price = float('inf')
                valid_tiers = [t for t in all_tiers if t.get("price_per_unit") is not None and t["price_per_unit"] > 0]
                
                for t in valid_tiers:
                    p = t["price_per_unit"]
                    if p < min_unit_price:
                        min_unit_price = p
                        best_tier = t
                    elif p == min_unit_price:
                        if best_tier is None or t["min_qty_units"] < best_tier["min_qty_units"]:
                            best_tier = t

                best_price_val = price_num
                best_qty = 1
                recommendation = "N/A"

                if best_tier:
                    best_qty = best_tier["min_qty_units"]
                    best_price_val = best_tier["price_per_unit"]
                    
                    if best_qty == 1:
                        recommendation = "Buy 1 bottle (Standard Price)"
                    else:
                        if best_qty % case_size == 0:
                            boxes = best_qty // case_size
                            recommendation = f"Buy {boxes} box(es) it will be good for you"
                        else:
                            recommendation = f"Buy {best_qty} bottle(s) it will be good for you"
                    
                    if best_qty > 1 and price_num and best_price_val < price_num:
                        savings = price_num - best_price_val
                        savings_pct = (savings / price_num) * 100
                        recommendation += f" (Save {savings_pct:.1f}% / ${savings:.2f} per bottle)"
                elif price_num:
                    recommendation = "Buy 1 bottle (Standard Price)"

                entry = {
                    "date":           today,
                    "name":           name,
                    "price":          price_num if price_num is not None else "N/A",
                    "case_size":      case_size,
                    "best_price":     best_price_val if best_price_val is not None else "N/A",
                    "best_qty":       best_qty,
                    "recommendation": recommendation,
                    "deal_text":      deal_text,
                    "category":       product.get("category", "General"),
                    "url":            page.url,
                    "all_tiers":      scraped_tiers
                }
                results.append(entry)

                price_str = f"${price_num:.2f}" if price_num else "N/A"
                best_str = f"${best_price_val:.2f}" if best_price_val else "N/A"
                log(f"    Base Price: {price_str} | Best Price: {best_str} | Recommendation: {recommendation}")

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
            current = float(entry["best_price"])
        except (ValueError, TypeError):
            continue

        prev = last_price(history, name)
        reasons = []
        is_deal = False

        if prev is not None and current < prev:
            drop_pct = ((prev - current) / prev) * 100
            if drop_pct >= ALERT_THRESHOLD_PCT:
                reasons.append(f"Historical Price dropped {drop_pct:.1f}% (was ${prev:.2f})")
                is_deal = True

        if prev is None:
            reasons.append("First time tracked")

        base_price = entry.get("price")
        if base_price and isinstance(base_price, (int, float)) and current < base_price:
            savings = base_price - current
            savings_pct = (savings / base_price) * 100
            reasons.append(f"Volume deal saves {savings_pct:.1f}% off base price (${base_price:.2f})")
            is_deal = True

        if entry.get("deal_text"):
            reasons.append(f"Promo: {entry['deal_text'][:80]}")
            is_deal = True

        rec = {
            **entry,
            "prev": prev,
            "reasons": reasons,
            "is_deal": is_deal,
            "history": price_history_list(history, name)
        }

        if is_deal:
            deals.append(rec)
        else:
            stable.append(rec)

    return deals, stable

# ─── OUTPUT REPORT ───────────────────────────────────────────────────────────

def get_sort_key(r):
    """
    Sorting rule:
    1. Active Discount Deals (where best_price < base_price and best_qty > 1) go first (is_stable = 0).
    2. Standard / Stable items go last (is_stable = 1).
    3. Within each group, sort by best_price in ascending order.
    """
    base = r.get("price")
    best = r.get("best_price")
    qty = r.get("best_qty", 1)
    
    is_deal = base and best and (best < base) and (qty > 1)
    is_stable = 0 if is_deal else 1
    
    try:
        price_val = float(best) if best not in (None, "N/A") else 999999.0
    except (ValueError, TypeError):
        price_val = 999999.0
        
    return (is_stable, price_val)

def print_ascii_table(results):
    """
    Prints a formatted ASCII table with active deals at the top, sorted by price.
    """
    sorted_results = sorted(results, key=get_sort_key)
    
    headers = ["Product Name", "Base Price", "Best Price", "Box/Case", "Recommendation"]
    widths = [35, 12, 12, 10, 45]
    
    def format_row(row):
        return f"| {row[0]:<{widths[0]}} | {row[1]:<{widths[1]}} | {row[2]:<{widths[2]}} | {row[3]:<{widths[3]}} | {row[4]:<{widths[4]}} |"
        
    border = f"+{'-'*(widths[0]+2)}+{'-'*(widths[1]+2)}+{'-'*(widths[2]+2)}+{'-'*(widths[3]+2)}+{'-'*(widths[4]+2)}+"
    
    lines = [border, format_row(headers), border]
    for r in sorted_results:
        name = r.get("name", "")
        if len(name) > widths[0]:
            name = name[:widths[0]-3] + "..."
            
        base = r.get("price")
        base_str = f"${base:.2f}" if isinstance(base, (int, float)) else str(base)
        
        best = r.get("best_price")
        best_str = f"${best:.2f}" if isinstance(best, (int, float)) else str(best)
        
        csize = r.get("case_size", 12)
        rec = r.get("recommendation", "N/A")
        if len(rec) > widths[4]:
            rec = rec[:widths[4]-3] + "..."
            
        row_data = [name, base_str, best_str, str(csize), rec]
        lines.append(format_row(row_data))
    lines.append(border)
    
    table_str = "\n".join(lines)
    log("\n" + "="*80)
    log("BEST PRICES & BUY RECOMMENDATIONS (Deals Prioritized First)")
    log("="*80)
    log("\n" + table_str)
    log("="*80 + "\n")

def write_html(results, run_time):
    """
    Writes a beautiful, mobile-friendly HTML report sorted with deals first, then stable.
    Includes direct order click links.
    """
    today = datetime.now().strftime("%B %d, %Y")
    sorted_results = sorted(results, key=get_sort_key)
    
    rows_html = ""
    deal_count = 0
    
    for r in sorted_results:
        name = r.get("name", "")
        category = r.get("category", "")
        base = r.get("price")
        base_str = f"${base:.2f}" if isinstance(base, (int, float)) else str(base)
        
        best = r.get("best_price")
        best_str = f"${best:.2f}" if isinstance(best, (int, float)) else "N/A"
        
        csize = r.get("case_size", 12)
        rec = r.get("recommendation", "N/A")
        url = r.get("url", "https://shop.sgproof.com")
        
        # Format pricing tiers
        tiers_list = r.get("all_tiers", [])
        tiers_desc = ""
        if tiers_list:
            tiers_desc = '<div class="tiers-box"><strong>Volume Discount Tiers:</strong><ul>'
            for t in tiers_list:
                qty_lbl = f"{t['min_qty_units']} bottles"
                if t['min_qty_units'] % csize == 0:
                    cases_num = t['min_qty_units'] // csize
                    qty_lbl = f"{cases_num} case{'s' if cases_num != 1 else ''} ({t['min_qty_units']} bottles)"
                disc_str = f" (save ${t['discount']:.2f})" if t.get("discount") else ""
                tiers_desc += f"<li>{qty_lbl} &rarr; <strong>${t['price_per_unit']:.2f}/bottle</strong>{disc_str}</li>"
            tiers_desc += '</ul></div>'
            
        # Highlight active discount deals
        is_deal = base and best and (best < base) and (r.get("best_qty", 1) > 1)
        highlight_class = "highlight-deal" if is_deal else ""
        badge_html = ""
        if is_deal:
            deal_count += 1
            diff = base - best
            saving_pct = (diff / base) * 100
            badge_html = f'<span class="saving-badge">-{saving_pct:.1f}%</span> <span class="deal-tag">DEAL</span>'
            
        rows_html += f'''
        <tr class="{highlight_class}">
            <td>
                <div class="prod-name"><a href="{url}" target="_blank" class="prod-link">{name}</a> {badge_html}</div>
                <div class="prod-cat">{category}</div>
                {tiers_desc}
            </td>
            <td>{base_str}</td>
            <td>{csize} bottles</td>
            <td class="best-price-col">
                <span class="best-price">${best_str}</span>
            </td>
            <td class="rec-col">{rec}</td>
            <td>
                <a href="{url}" target="_blank" class="buy-btn">Order on SGProof &rarr;</a>
            </td>
        </tr>
        '''
        
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SGProof Deal Report — {today}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f8fafc;
    color: #0f172a;
    padding: 32px 16px;
  }}
  .container {{
    max-width: 1250px;
    margin: 0 auto;
    background: #ffffff;
    border-radius: 16px;
    box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.03), 0 8px 10px -6px rgba(0, 0, 0, 0.03);
    padding: 32px;
    border: 1px solid #e2e8f0;
  }}
  .header {{
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 24px;
    margin-bottom: 24px;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
  }}
  .header-left h1 {{
    font-size: 28px;
    font-weight: 800;
    color: #1e293b;
    letter-spacing: -0.025em;
  }}
  .header-left p {{
    font-size: 14px;
    color: #64748b;
    margin-top: 6px;
  }}
  .stats-container {{
    display: flex;
    gap: 16px;
    margin-bottom: 28px;
  }}
  .stat-card {{
    background: #f1f5f9;
    border-radius: 12px;
    padding: 16px 24px;
    min-width: 180px;
    border: 1px solid #e2e8f0;
  }}
  .stat-card.deal-highlight {{
    background: #f0fdf4;
    border-color: #bbf7d0;
  }}
  .stat-card.deal-highlight .stat-num {{
    color: #15803d;
  }}
  .stat-num {{
    font-size: 26px;
    font-weight: 700;
    color: #0f172a;
  }}
  .stat-lbl {{
    font-size: 12px;
    color: #64748b;
    font-weight: 500;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  .table-responsive {{
    overflow-x: auto;
    border-radius: 8px;
    border: 1px solid #e2e8f0;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    text-align: left;
  }}
  th {{
    background: #f8fafc;
    color: #475569;
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 14px 16px;
    border-bottom: 2px solid #e2e8f0;
  }}
  td {{
    padding: 16px;
    border-bottom: 1px solid #e2e8f0;
    vertical-align: top;
    font-size: 14px;
  }}
  tr:hover {{
    background: #f8fafc;
  }}
  .prod-name {{
    font-weight: 600;
    color: #0f172a;
    font-size: 16px;
  }}
  .prod-link {{
    color: #0284c7;
    text-decoration: none;
    border-bottom: 1px dashed #bae6fd;
    transition: color 0.1s ease;
  }}
  .prod-link:hover {{
    color: #0369a1;
    border-bottom-style: solid;
  }}
  .prod-cat {{
    font-size: 12px;
    color: #64748b;
    margin-top: 4px;
  }}
  .tiers-box {{
    margin-top: 8px;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 12px;
    color: #475569;
    max-width: 450px;
  }}
  .tiers-box strong {{
    color: #1e293b;
  }}
  .tiers-box ul {{
    list-style: none;
    margin-top: 6px;
  }}
  .tiers-box li {{
    margin: 4px 0;
    border-bottom: 1px dashed #e2e8f0;
    padding-bottom: 4px;
  }}
  .tiers-box li:last-child {{
    border-bottom: none;
    padding-bottom: 0;
  }}
  .best-price {{
    font-weight: 700;
    font-size: 18px;
    color: #0f172a;
  }}
  .rec-col {{
    font-weight: 600;
    color: #334155;
    max-width: 280px;
  }}
  .buy-btn {{
    display: inline-block;
    background: #10b981;
    color: white;
    text-decoration: none;
    padding: 8px 14px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 700;
    transition: background 0.15s ease;
    white-space: nowrap;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  }}
  .buy-btn:hover {{
    background: #059669;
  }}
  .highlight-deal {{
    background: #f0fdf4;
  }}
  .highlight-deal:hover {{
    background: #dcfce7;
  }}
  .highlight-deal .best-price {{
    color: #15803d;
  }}
  .highlight-deal .rec-col {{
    color: #15803d;
  }}
  .saving-badge {{
    background: #dcfce7;
    color: #15803d;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 4px;
    margin-left: 6px;
    vertical-align: middle;
  }}
  .deal-tag {{
    background: #10b981;
    color: white;
    font-size: 10px;
    font-weight: 800;
    padding: 2px 6px;
    border-radius: 4px;
    margin-left: 4px;
    vertical-align: middle;
    letter-spacing: 0.05em;
  }}
  .footer {{
    margin-top: 32px;
    border-top: 1px solid #e2e8f0;
    padding-top: 16px;
    font-size: 12px;
    color: #94a3b8;
    text-align: center;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="header-left">
      <h1>SGProof Buy Decisions & Recommendations</h1>
      <p>Compiled on {today} at {run_time} · Active Deals Prioritized at the Top</p>
    </div>
  </div>

  <div class="stats-container">
    <div class="stat-card deal-highlight">
      <div class="stat-num">{deal_count}</div>
      <div class="stat-lbl">Active Tiers Deals found</div>
    </div>
    <div class="stat-card">
      <div class="stat-num">{len(results)}</div>
      <div class="stat-lbl">Tracked Items Searched</div>
    </div>
  </div>

  <div class="table-responsive">
    <table>
      <thead>
        <tr>
          <th>Product details (Click link to order)</th>
          <th>Base Price</th>
          <th>Case Size</th>
          <th>Best Price</th>
          <th>Recommended buy quantity</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        {rows_html if rows_html else '<tr><td colspan="6" style="text-align:center;padding:24px;color:#888;">No items found.</td></tr>'}
      </tbody>
    </table>
  </div>

  <div class="footer">
    Generated by SGProof Deal Tracker. Historical logs are stored in price_history.csv.
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

    products = load_products()
    if not products:
        log("No products to scrape. Please configure products.csv")
        return
        
    if not SGPROOF_EMAIL or not SGPROOF_PASSWORD:
        log("ERROR: Set SGPROOF_EMAIL and SGPROOF_PASSWORD in your .env file")
        sys.exit(1)

    history = load_history()
    results = scrape_products(products)

    if not results:
        log("WARNING: No results scraped! The email will be sent but it will be empty.")

    # Store findings in history
    for entry in results:
        save_entry(entry)

    run_time = datetime.now().strftime("%I:%M %p")

    # Render terminal output
    print_ascii_table(results)

    # Render HTML dashboard
    write_html(results, run_time)

    log("="*50)
    log(f"Scraped details completed for {len(results)} items.")
    log("Open deals_today.html in your browser to inspect visual recommendations.")
    log("="*50)

if __name__ == "__main__":
    main()

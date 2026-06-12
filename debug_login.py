from playwright.sync_api import sync_playwright
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os
EMAIL = os.getenv("SGPROOF_EMAIL", "")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=800)
    page = browser.new_page()

    print("Step 1: Opening SGProof...")
    page.goto("https://shop.sgproof.com/", timeout=40000)
    page.wait_for_timeout(4000)
    page.screenshot(path="step1_homepage.png")
    print("Screenshot saved: step1_homepage.png")

    print("Step 2: Looking for age gate...")
    try:
        yes = page.locator("button:has-text('Yes'), a:has-text('Yes')").first
        if yes.is_visible(timeout=4000):
            print("  Age gate found — clicking Yes")
            yes.click()
            page.wait_for_timeout(3000)
        else:
            print("  No age gate visible")
    except Exception as e:
        print(f"  Age gate check: {e}")

    page.screenshot(path="step2_after_agegate.png")
    print("Screenshot saved: step2_after_agegate.png")

    print("Step 3: Looking for state selector...")
    try:
        state_sel = page.locator("select, [class*='state'], [class*='market'], [class*='region']").first
        if state_sel.is_visible(timeout=3000):
            print("  State/market selector found")
            page.screenshot(path="step3_state_selector.png")
            print("Screenshot saved: step3_state_selector.png")
    except Exception:
        print("  No state selector found")

    print("Step 4: Clicking Log In...")
    try:
        login_btn = page.locator("a:has-text('Log In'), button:has-text('Log In')").first
        if login_btn.is_visible(timeout=5000):
            login_btn.click()
            print("  Clicked Log In")
            page.wait_for_timeout(4000)
        else:
            print("  Log In button not visible")
    except Exception as e:
        print(f"  Login click error: {e}")

    page.screenshot(path="step4_after_login_click.png")
    print("Screenshot saved: step4_after_login_click.png")

    print("Step 5: Checking all inputs on page...")
    inputs = page.locator("input").all()
    print(f"  Found {len(inputs)} input elements:")
    for i, inp in enumerate(inputs):
        try:
            t  = inp.get_attribute("type") or "?"
            n  = inp.get_attribute("name") or "?"
            gn = inp.get_attribute("data-gigya-name") or "?"
            vis = inp.is_visible()
            print(f"    [{i}] type={t} name={n} gigya={gn} visible={vis}")
        except Exception:
            pass

    print("\nDone — check the screenshot PNG files in your sgproof_tracker folder")
    browser.close()

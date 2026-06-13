import sys
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        print("Navigating to homepage...")
        page.goto("https://shop.sgproof.com/", timeout=45000)
        page.wait_for_timeout(3000)
        
        # Bypass age gate
        print("Bypassing age gate...")
        modal = page.locator("#ageGatemodal")
        if modal.is_visible(timeout=8000):
            state_select = modal.locator("select").first
            try:
                state_select.select_option("TX")
            except:
                state_select.select_option(label="Texas")
            page.wait_for_timeout(1000)
            yes_btn = modal.locator("button:has-text('Yes')").first
            if not yes_btn.is_visible():
                yes_btn = modal.get_by_role("button", name="Yes").first
            yes_btn.click()
            page.wait_for_timeout(3000)
            
        print("Logging in...")
        page.get_by_role("button", name="Log In").first.click()
        page.wait_for_timeout(1000)
        page.locator("#email").fill("lonestar.1019greenville@gmail.com")
        page.locator("#password").fill("1QAZ@wsx3EDC$rfv")
        page.get_by_role("button", name="Log in", exact=True).first.click()
        page.wait_for_timeout(8000)
        
        page.screenshot(path="homepage_after_login.png")
        print("Screenshot saved to homepage_after_login.png")
        
        # Try to find any search icons
        icons = page.locator("svg").all()
        print(f"Found {len(icons)} svg icons.")
        for icon in icons:
            print(icon.get_attribute("aria-label") or icon.get_attribute("class") or icon.get_attribute("data-testid") or "icon")
            
        with open("homepage.html", "w") as f:
            f.write(page.content())
            
        browser.close()

if __name__ == "__main__":
    main()

# debug_captcha.py
import os
import time
import logging
from playwright.sync_api import sync_playwright
from app.shared.captcha_handle_cm import solve_captcha

def main():
    print("Starting debug_captcha.py...")
    
    # Path to local cap.html
    cwd = os.getcwd()
    file_path = f"file:///{cwd}/cap.html".replace("\\", "/")
    print(f"Loading: {file_path}")

    with sync_playwright() as p:
        # Launch headed to see it
        browser = p.firefox.launch(headless=False)
        page = browser.new_page()
        
        try:
            page.goto(file_path)
            print("Page loaded.")
            
            # Wait a sec
            time.sleep(2)
            
            print("Calling solve_captcha...")
            found = solve_captcha(page)
            
            if found:
                print("SUCCESS: Captcha solved (clicked).")
            else:
                print("FAILURE: Captcha not found or not clicked.")
                
            print("Waiting 10s to observe...")
            time.sleep(10)
            
        except Exception as e:
            print(f"Error: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    main()

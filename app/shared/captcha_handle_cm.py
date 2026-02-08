# app/shared/captcha_handle_cm.py
import random
import time
import logging
from playwright.sync_api import Page, Locator

def geometry_click(locator: Locator) -> bool:
    """
    Performs a 'human-like' click at a random point within the element
    utilizing Gaussian distribution to bias towards the center.
    """
    try:
        if not locator.is_visible():
            return False

        # Get bounding box (width/height are needed for calculation)
        box = locator.bounding_box()
        if not box:
            logging.warning(f"Could not get bounding box for {locator}")
            return False

        w, h = box['width'], box['height']

        # Calculate random point with Gaussian distribution (clipper to box dimensions)
        # We calculate offset relative to the element (0,0 is top-left of element)
        target_x = random.gauss(w / 2, w / 6)
        target_y = random.gauss(h / 2, h / 6)

        # Clamp to ensure it's inside the box
        target_x = max(0, min(w, target_x))
        target_y = max(0, min(h, target_y))

        logging.info(f"Geometry click on {locator} at offset ({target_x:.1f}, {target_y:.1f})")

        # Use locator.click with position offset + delay
        # This handles coordinate translation (even in iframes) and basic down/up delay
        locator.click(
            position={"x": target_x, "y": target_y},
            delay=random.uniform(100, 300) # milliseconds
        )
        
        return True

    except Exception as e:
        logging.error(f"Error in geometry_click: {e}")
        return False

def solve_captcha(page: Page) -> bool:
    """
    Detects and attempts to solve the captcha.
    Checks main page and all iframes.
    """
    # Selectors to try
    selectors = [
        ".cb-lb",              
        ".cb-c",               
        "input[type='checkbox'][name*='turnstile']", 
        "#challenge-stage input[type='checkbox']",
        "iframe[src*='challenges']" # generic iframe detector
    ]

    # Debug: Print frame count
    # print(f"[captcha] Checking {len(page.frames)} frames for captcha...")

    # 1. Check main page
    for sel in selectors:
        if page.locator(sel).first.is_visible():
            print(f"[captcha] FOUND on main page: {sel}")
            return _attempt_solve(page.locator(sel).first)

    # 2. Check frames
    for i, frame in enumerate(page.frames):
        try:
            # Debug log for frame
            # url_short = frame.url[:60] + "..." if len(frame.url) > 60 else frame.url
            # print(f"[captcha] Checking frame {i}: {url_short}")
            
            # 2a. Check selectors inside frame
            for sel in selectors:
                if frame.locator(sel).first.is_visible():
                    print(f"[captcha] FOUND in frame {i}: {sel}")
                    return _attempt_solve(frame.locator(sel).first)
            
            # 2b. Cloudflare/Turnstile Fallback: Click body if it's the right frame
            # The widget is usually small (e.g. 300x65), clicking the center of body works
            if "challenge-platform" in frame.url or "turnstile" in frame.url:
                # print(f"[captcha] Frame {i} looks like Cloudflare. Trying body fallback...")
                body = frame.locator("body")
                if body.is_visible():
                    return _attempt_solve(body)

        except Exception:
            continue
            
    # Debug: Take screenshot if still stuck to see what's happening
    try:
        page.screenshot(path="debug_captcha_view.png")
        # print("[captcha] No captcha found. Saved screenshot to debug_captcha_view.png")
    except Exception:
        pass
    
    return False

def _attempt_solve(locator: Locator) -> bool:
    print(f"[captcha] Attempting geometry click on {locator}...")
    time.sleep(random.uniform(0.5, 1.5))
    
    if geometry_click(locator):
        print("[captcha] Click sent. Waiting...")
        time.sleep(3.0) 
        return True
    return False

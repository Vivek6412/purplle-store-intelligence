import asyncio
from playwright.async_api import async_playwright

async def monitor_dashboard():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        print("Opening dashboard...")
        await page.goto("http://localhost:8000/dashboard/")
        
        # Select ST1008 from the dropdown if it's not already selected
        print("Selecting ST1008 store...")
        try:
            await page.select_option("select#storeSelect", "ST1008")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"Could not select store: {e}")

        print("Monitoring for connection and updates (waiting up to 180 seconds)...")
        
        is_connected = False
        updates_detected = False
        
        for i in range(90):
            # Check if RECONNECTING is gone and it says LIVE or similar
            try:
                status_text = await page.locator("#liveLabel").inner_text()
                if "LIVE" in status_text.upper():
                    is_connected = True
            except:
                pass
                
            # Check unique visitors
            try:
                visitors_text = await page.locator("#kvVisitors").inner_text()
                if visitors_text and visitors_text != "—":
                    visitors = int(visitors_text)
                    if visitors > 0:
                        updates_detected = True
                        print(f"\n--- SUCCESS! ---")
                        print(f"Dashboard connected to API and successfully rendered incoming telemetry!")
                        print(f"Unique visitors tracked: {visitors}")
                        
                        # Also check the heatmap to verify zone names rendered
                        try:
                            # Actually, heatmap zones are drawn on canvas, so we can't easily read them.
                            pass
                        except:
                            pass
                            
                        break
            except:
                pass
                
            print(f"Checking... (Attempt {i+1}/90) - Connected: {is_connected}")
            await asyncio.sleep(2)
            
        if not updates_detected:
            print("Dashboard did not update within the timeout period.")
            if not is_connected:
                print("It never successfully connected to the API.")
                
        await browser.close()

if __name__ == "__main__":
    asyncio.run(monitor_dashboard())

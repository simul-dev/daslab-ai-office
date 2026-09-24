"""Optional UI smoke check. Requires Playwright; never calls the AI run endpoint."""
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    root = Path(__file__).resolve().parents[1]
    out = root / "test-results"
    out.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1512, "height": 1100}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:8765", wait_until="networkidle")
        page.wait_for_selector(".task-card")
        page.wait_for_selector(".review-box")
        assert page.locator("#worker-title").inner_text() == "Codex 실제 작업자 연결"
        assert page.locator("#count-review").inner_text() == "1"
        assert page.locator("#task-detail h2").inner_text() == "프랜차이즈 출점 지원 MVP의 첫 기능 제안"
        page.screenshot(path=str(out / "office-desktop.png"))
        report = page.locator(".artifact").filter(has=page.locator(".file-name", has_text="report.md"))
        report.get_by_role("button", name="보기", exact=True).click()
        expect(page.locator("#artifact-content")).to_contain_text("공사 견적 비교표 작성")
        assert "고객 업무 검증이나 구현은 수행하지 않았다" in page.locator("#artifact-content").inner_text()
        page.screenshot(path=str(out / "office-report.png"))
        page.locator("#close-artifact").click()
        page.locator("#sample-task").click()
        expect(page.locator("[name=title]")).to_have_value(re.compile(".+"))
        assert page.locator("[name=acceptance_criteria]").input_value().count("\n") == 5
        page.locator("#task-dialog .close-dialog").first.click()
        page.locator("#status-filter").select_option("review")
        assert page.locator(".task-card").count() == 1
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(out / "office-mobile.png"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile horizontal overflow"
        assert not errors, errors
        browser.close()
    (out / "browser-check.json").write_text(json.dumps({"passed": True, "errors": errors,
        "checks": ["real worker status", "saved review task", "report preview", "sample form", "status filter", "mobile width"]}, indent=2), encoding="utf-8")
    print("Browser smoke passed: real task, report preview, sample form, filter, mobile; no page errors.")


if __name__ == "__main__":
    main()

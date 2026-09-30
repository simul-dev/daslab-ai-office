"""Optional local UI smoke script; requires Playwright and the test fixture server.

1. Run python tests/ui_fixture_server.py in a separate terminal.
2. Run python tests/browser_check.py if Playwright is installed.
This script only accepts the no-AI fixture at localhost:8766, never production.
The supported Codex browser tools are used for agent-driven UI verification.
"""
import json
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4


BASE_URL = "http://127.0.0.1:8766"


def main():
    with urlopen(BASE_URL + "/api/health", timeout=3) as response:
        health = json.load(response)
    if health.get("worker", {}).get("provider") != "ui_fixture_no_ai":
        raise RuntimeError("화면 검증 전용 서버만 사용할 수 있습니다. tests/ui_fixture_server.py를 실행하세요.")

    from playwright.sync_api import sync_playwright, expect

    out = Path(__file__).resolve().parents[1] / "test-results"
    out.mkdir(exist_ok=True)
    errors = []
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1512, "height": 1100}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_selector(".mission-row")
        expect(page.locator("#report-dialog")).not_to_be_visible()
        assert "result.json" not in page.locator("body").inner_text()
        page.screenshot(path=str(out / "mission-desktop.png"), full_page=True)
        checks.append("summary dashboard, no automatic report opening or visible JSON")

        page.locator(".mission-row").filter(has_text="부분 달성 예시").click()
        expect(page.locator("#report-dialog")).to_be_visible()
        expect(page.locator("#task-detail")).to_contain_text("남은 일")
        details = page.locator('details[data-key="report"]')
        assert not details.evaluate("(node) => node.open")
        details.locator("summary").click()
        assert details.evaluate("(node) => node.open")
        expect(details).to_contain_text("실제 AI 분석 결과가 아닙니다")
        page.screenshot(path=str(out / "mission-report.png"), full_page=True)
        page.locator("#close-report").click()
        checks.append("readable report, remaining work, optional full report")

        page.locator("#status-filter").select_option("achieved")
        expect(page.locator("#list-count")).to_have_text(page.locator("#count-achieved").inner_text())
        assert all("달성" in row for row in page.locator(".mission-row").all_inner_texts())
        page.locator("#status-filter").select_option("")
        checks.append("outcome filter")

        page.locator("#new-mission").click()
        expect(page.locator("#mission-dialog")).to_be_visible()
        expect(page.locator("#mission-form [required]")).to_have_count(1)
        assert not page.locator(".context-input").evaluate("(node) => node.open")
        label = "[화면 검증] 한 문장 입력 " + uuid4().hex[:6]
        page.locator("#mission").fill(label)
        page.locator("#submit-mission").click()
        expect(page.locator("#mission-dialog")).not_to_be_visible()
        submitted = page.locator(".mission-row").filter(has_text=label)
        expect(submitted).to_contain_text("진행 중", timeout=10000)
        expect(submitted).to_contain_text("100%", timeout=45000)
        checks.append("one required mission input, fake worker running to achieved")

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile horizontal overflow"
        page.screenshot(path=str(out / "mission-mobile.png"), full_page=True)
        checks.append("390px mobile without horizontal overflow")
        assert not errors, errors
        browser.close()
    (out / "mission-browser-check.json").write_text(json.dumps(
        {"passed": True, "fixture": BASE_URL, "real_ai_called": False, "errors": errors, "checks": checks},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("UI fixture smoke passed. No real AI or production data was used.")


if __name__ == "__main__":
    main()

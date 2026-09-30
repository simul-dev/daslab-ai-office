"""Independent browser checks for the documented supply-chain demo contract.

These are synthetic conservation/edge-case checks, not industrial validation.
Generated model.test.cjs is never executed on the host.
"""
import asyncio
import re
import time
from datetime import datetime, timezone

from .browser_verification import (BrowserVerifier, BrowserUnavailable, _preview_url,
                                   _origin, _allowed_request, _installed_browser, _output_directory)


class PrototypeVerifier(BrowserVerifier):
    def verify(self, url, artifacts, output_dir, cancel_event=None):
        started = time.monotonic()
        receipt = {"status": "failed", "kind": "supply_chain_model_browser_checks",
                   "started_at": datetime.now(timezone.utc).isoformat(), "artifacts": artifacts,
                   "checks": [], "errors": [], "screenshots": [], "business_acceptance": False,
                   "scope": "합성 공급망 모델의 보존식·경계조건·재현성과 화면 입력·반응형 표시"}
        try:
            _preview_url(url)
            if not isinstance(artifacts, dict) or not {"index.html", "model.js", "app.js"} <= artifacts.keys():
                raise ValueError("Pinned prototype files are required")
            if any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for v in artifacts.values()):
                raise ValueError("Invalid prototype hash")
            output = _output_directory(output_dir) / "prototype-browser"
            output.mkdir(exist_ok=False)
            asyncio.run(self._run_bounded(url, output, receipt, cancel_event))
            if receipt["checks"] and not receipt["errors"] and all(c["status"] == "passed" for c in receipt["checks"]):
                receipt["status"] = "passed"
        except BrowserUnavailable as exc:
            receipt.update(status="unavailable")
            receipt["errors"].append(str(exc))
        except Exception as exc:
            receipt["errors"].append(str(exc)[:500] or type(exc).__name__)
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 2)
        receipt["summary"] = "모델 기본 보존식과 실제 화면 동작을 확인했습니다. 현장 적합성·3D 품질은 별도 검수입니다." if receipt["status"] == "passed" else "데모의 모델·화면 검사를 통과하지 못했습니다."
        return receipt

    async def _run_browser(self, url, output, receipt):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise BrowserUnavailable("격리 브라우저 검증 도구가 필요합니다.") from None
        expected_origin = _origin(url)
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=True, channel=_installed_browser(), timeout=12000,
                    args=["--disable-background-networking", "--disable-component-update", "--dns-prefetch-disable"])
            except Exception as exc:
                raise BrowserUnavailable("격리 브라우저 실행 실패: " + str(exc)[:150]) from exc
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 960}, permissions=[], service_workers="block")
                async def guard(route):
                    if _allowed_request(route.request.url, route.request.method, expected_origin):
                        await route.continue_()
                    else:
                        receipt["errors"].append("범위 밖 네트워크 요청 차단")
                        await route.abort()
                await context.route("**/*", guard)
                page = await context.new_page()
                page.set_default_timeout(7000)
                page.on("pageerror", lambda error: receipt["errors"].append(str(error)[:200]))
                await page.goto(url, wait_until="load")
                checks = await page.evaluate("""() => {
                    const run=window.DASModel.run, rows=[];
                    const check=(name,pass)=>rows.push({name,status:pass?'passed':'failed'});
                    for(const seed of [1,42,987]) {
                        const a=run({seed}), b=run({seed});
                        check('동일 입력 재현성 '+seed,JSON.stringify(a)===JSON.stringify(b));
                        check('재고 보존 '+seed,a.parameters.initialStock+a.received===a.stock+a.allocated);
                        check('주문 보존 '+seed,a.arrivals===a.completed+a.backlog+a.inService);
                        check('수요 건수 '+seed,a.arrivals===a.parameters.days*a.parameters.ordersPerDay);
                        check('음수·비유한 값 없음 '+seed,['arrivals','completed','backlog','inService','stock','received','allocated','incoming'].every(k=>Number.isFinite(a[k])&&a[k]>=0));
                    }
                    const noDemand=run({ordersPerDay:0}),noStock=run({initialStock:0,batch:0});
                    check('수요 0',noDemand.arrivals===0&&noDemand.completed===0&&noDemand.backlog===0);
                    check('공급 0',noStock.completed===0&&noStock.backlog===noStock.arrivals&&noStock.arrivals>0);
                    const input={initialStock:1000,batch:0,ordersPerDay:40,serviceTime:0.2};
                    const slow=run({...input,servers:1}),fast=run({...input,servers:2});
                    check('용량 증가 시 처리 증가',fast.completed>slow.completed&&fast.backlog<slow.backlog);
                    return rows;
                }""")
                receipt["checks"].extend(checks)
                await page.locator("#stock").fill("0")
                await page.locator("#batch").fill("0")
                await page.locator("#run-model").click()
                displayed = await page.locator("#model-results").get_attribute("data-completed")
                receipt["checks"].append({"name": "화면 입력이 모델 결과에 반영", "status": "passed" if displayed == "0" else "failed"})
                for width, name in ((1280, "desktop"), (390, "mobile")):
                    await page.set_viewport_size({"width": width, "height": 960})
                    overflow = await page.evaluate("document.documentElement.scrollWidth > innerWidth + 2")
                    receipt["checks"].append({"name": name + " 가로 넘침 없음", "status": "failed" if overflow else "passed"})
                    path = output / (name + ".png")
                    await page.screenshot(path=str(path), full_page=True)
                    receipt["screenshots"].append({"name": name, "path": str(path)})
            finally:
                await browser.close()

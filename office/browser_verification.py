"""Read-only, isolated browser smoke verification for a pinned office preview.

This proves the listed UI interactions, not design quality or business success.
The caller owns preview creation and revalidates the pinned source before delivery.
"""
import asyncio
import copy
import importlib.util
import os
import re
import time
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


class BrowserUnavailable(RuntimeError):
    pass


def _origin(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1')
            or parsed.username is not None or parsed.password is not None
            or parsed.port is None or not 1024 <= parsed.port <= 65535):
        raise ValueError('Browser verification requires an explicit loopback preview port')
    return parsed.scheme, parsed.hostname, parsed.port


def _preview_url(url):
    _origin(url)
    parsed = urlsplit(url)
    if parsed.path != '/' or parsed.query or parsed.fragment:
        raise ValueError('Browser verification requires the preview root URL')
    return url


def _allowed_request(url, method, expected_origin):
    try:
        return method in ('GET', 'HEAD') and _origin(url) == expected_origin
    except (ValueError, TypeError):
        return False


def _artifacts(value):
    if not isinstance(value, dict) or not {'office.html', 'office.css', 'office.js'} <= set(value):
        raise ValueError('Pinned office HTML, CSS and JavaScript hashes are required')
    if len(value) > 512:
        raise ValueError('Too many preview artifacts')
    for name, digest in value.items():
        parts = name.split('/') if isinstance(name, str) else []
        if (not parts or any(not part or part in ('.', '..') or ':' in part or '\\' in part
                             or part.endswith((' ', '.')) for part in parts)
                or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)
                or (name not in ('office.html', 'office.css', 'office.js')
                    and not (name.startswith('brand/') and Path(name).suffix.lower()
                             in {'.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.ico', '.woff2'}))):
            raise ValueError('Invalid pinned preview artifact')
    return copy.deepcopy(value)


def _output_directory(directory):
    directory = Path(directory).absolute()
    for part in (directory, *directory.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Browser evidence directory must not use links or junctions')
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _installed_browser():
    # A new temporary browser profile is still used with an installed executable.
    candidates = [
        ('msedge', Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'),
        ('msedge', Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Microsoft/Edge/Application/msedge.exe'),
        ('chrome', Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe'),
    ]
    return next((channel for channel, path in candidates if path.is_file()), None)


async def _route_request(route, expected_origin, blocked, http_errors):
    request = route.request
    if not _allowed_request(request.url, request.method, expected_origin):
        blocked.append(request.method + ' ' + request.url)
        await route.abort('blockedbyclient')
        return
    try:
        parsed = urlsplit(request.url)
        if (request.method == 'GET' and request.resource_type == 'eventsource'
                and parsed.path == '/api/org/events' and not parsed.query and not parsed.fragment):
            # Only DevelopmentWorkspace.open_preview supplies this URL. Its fixed
            # server handler streams snapshots and has no redirect path. Preserve
            # the real connection: a finite response falsely puts the UI offline.
            # Every other request is fetched below without following redirects.
            await route.continue_()
            return
        response = await route.fetch(max_redirects=0, timeout=5000)
        if 300 <= response.status < 400:
            blocked.append('Redirect blocked: ' + request.url)
            await route.abort('blockedbyclient')
        else:
            if response.status >= 400 and parsed.path != '/favicon.ico':
                http_errors.append(f'{response.status} {request.url}')
            await route.fulfill(response=response)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        http_errors.append(request.url + ': ' + str(exc)[:250])
        await route.abort('failed')


async def _keyboard_skip_link(page, output_dir, receipt, label):
    """Exercise native Tab/Enter, without programmatically focusing the target."""
    link = page.locator('a.skip-link[href="#workspace"]')
    if await link.count() != 1:
        raise ValueError('Keyboard skip link: expected one a.skip-link[href="#workspace"]')
    await page.keyboard.press('Tab')
    # Let the focus-reveal style finish its layout before measuring visibility.
    await page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    focused = await link.evaluate('''(link) => {
        const rect = link.getBoundingClientRect();
        const active = document.activeElement;
        const center = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
        return {
            focused: active === link, focusVisible: link.matches(':focus-visible'),
            visible: link.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}),
            inViewport: rect.width > 0 && rect.height > 0 && rect.left >= 0 && rect.top >= 0
                && rect.right <= innerWidth && rect.bottom <= innerHeight,
            unobscured: center === link || link.contains(center),
            active: active ? active.tagName.toLowerCase() + (active.id ? '#' + active.id : '') : 'none'
        };
    }''')
    target = output_dir / (label + '-keyboard.png')
    if target.is_symlink() or target.exists():
        raise ValueError('Browser evidence file already exists: ' + target.name)
    await page.screenshot(path=str(target), full_page=False, timeout=5000)
    receipt['screenshots'].append(str(target))
    if not focused['focused']:
        raise ValueError('Keyboard Tab did not focus the skip link; document.activeElement=' + focused['active'])
    if not focused['focusVisible']:
        raise ValueError('Keyboard skip link received Tab focus but did not match :focus-visible')
    for key, description in (('visible', 'visible'), ('inViewport', 'fully inside the viewport'),
                             ('unobscured', 'unobscured at its center')):
        if not focused[key]:
            raise ValueError('Keyboard skip link received Tab focus but was not ' + description)
    await page.keyboard.press('Enter')
    try:
        await page.wait_for_function('document.activeElement === document.querySelector("main#workspace")')
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        active = await page.evaluate('''(() => {
            const active = document.activeElement;
            return active ? active.tagName.toLowerCase() + (active.id ? '#' + active.id : '') : 'none';
        })()''')
        raise ValueError('Keyboard Enter on the skip link did not move actual focus to main#workspace; '
                         'document.activeElement=' + active) from exc
    return (label + ': Actual keyboard Tab focused a.skip-link[href="#workspace"] with :focus-visible=true; '
            'the link was visible, fully inside the viewport and unobscured. '
            'Actual keyboard Enter moved document.activeElement to main#workspace; '
            'no script focus() or click was used.')


class BrowserVerifier:
    def __init__(self, timeout_seconds=60, runner=None):
        self.timeout_seconds = max(1, min(float(timeout_seconds), 90))
        self.runner = runner or self._run_browser

    def verify(self, url, artifacts, output_dir, cancel_event=None):
        started = time.monotonic()
        receipt = {
            'status': 'failed', 'kind': 'office_ui_browser_smoke', 'url': url,
            'started_at': datetime.now(timezone.utc).isoformat(),
            'artifacts': {}, 'checks': [], 'errors': [], 'screenshots': [],
            'aesthetic_acceptance': False, 'business_acceptance': False,
            'scope': '읽기 전용 화면 표시·키보드 본문 이동·직원 선택·탭·반응형 배치·미션 상세 점검',
        }
        try:
            url = _preview_url(url)
            receipt['artifacts'] = _artifacts(artifacts)
            output_dir = _output_directory(output_dir) / ('browser-' + uuid4().hex[:12])
            output_dir.mkdir()
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError('Browser verification cancelled')
            asyncio.run(self._run_bounded(url, output_dir, receipt, cancel_event))
            if not receipt['checks'] or not any(c.get('status') == 'passed' for c in receipt['checks']):
                raise RuntimeError('Browser verification did not run any checks')
            if any(c.get('status') == 'failed' for c in receipt['checks']) or receipt['errors']:
                receipt['status'] = 'failed'
            else:
                receipt['status'] = 'passed'
        except BrowserUnavailable as exc:
            receipt['status'] = 'unavailable'
            receipt['errors'].append(str(exc))
        except (Exception, asyncio.CancelledError) as exc:
            receipt['errors'].append(str(exc) or type(exc).__name__)
        receipt['elapsed_seconds'] = round(time.monotonic() - started, 3)
        receipt['summary'] = {
            'passed': '실제 브라우저의 기본 화면·읽기 전용 동작 점검을 통과했습니다. 디자인 품질·사업 성과 판단은 별도입니다.',
            'failed': '브라우저 점검을 통과하지 못했습니다. 실패 항목을 고친 뒤 다시 확인해야 합니다.',
            'unavailable': '브라우저 점검 도구를 실행하지 못했습니다. 실행 환경을 준비한 뒤 다시 확인해야 합니다.',
        }[receipt['status']]
        return receipt

    async def _run_bounded(self, url, output_dir, receipt, cancel_event):
        task = asyncio.create_task(self.runner(url, output_dir, receipt))
        started = time.monotonic()
        try:
            while not task.done():
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError('Browser verification cancelled')
                if time.monotonic() - started >= self.timeout_seconds:
                    raise TimeoutError('Browser verification exceeded its time limit')
                await asyncio.wait({task}, timeout=.1)
            await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=8)
                except (asyncio.CancelledError, TimeoutError):
                    pass

    async def _run_browser(self, url, output_dir, receipt):
        if importlib.util.find_spec('playwright') is None:
            raise BrowserUnavailable('Install the optional verifier: python -m pip install -r requirements-browser.txt. An installed Edge/Chrome can be used without downloading a browser.')
        from playwright.async_api import async_playwright

        expected_origin = _origin(url)
        blocked, page_errors, http_errors = [], [], []
        browser = None
        manager = async_playwright()
        playwright = await manager.start()
        try:
            channel = _installed_browser()
            try:
                browser = await playwright.chromium.launch(
                    headless=True, channel=channel, timeout=12000,
                    args=['--disable-background-networking', '--disable-component-update',
                          '--dns-prefetch-disable',
                          '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
            except Exception as exc:
                raise BrowserUnavailable('No usable isolated Chromium browser. Install Edge/Chrome or run python -m playwright install chromium. ' + str(exc)[:300]) from exc
            receipt['browser'] = {'engine': 'chromium', 'channel': channel or 'bundled', 'version': browser.version}
            context = await browser.new_context(
                viewport={'width': 1440, 'height': 1000}, permissions=[],
                accept_downloads=False, service_workers='block')
            if not hasattr(context, 'route_web_socket'):
                raise BrowserUnavailable('Playwright >= 1.48 is required to block WebSocket connections')
            context.set_default_timeout(5000)
            context.set_default_navigation_timeout(10000)

            async def route_request(route):
                await _route_request(route, expected_origin, blocked, http_errors)

            def block_socket(socket):
                blocked.append('WebSocket blocked: ' + socket.url)
                # Omitting connect_to_server/connect never establishes a network socket.

            await context.route('**/*', route_request)
            await context.route_web_socket('**/*', block_socket)
            await context.add_init_script('''(() => {
                for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection', 'WebTransport']) {
                    Object.defineProperty(globalThis, name, {configurable: false, writable: false,
                        value: function () { throw new Error('Unavailable in read-only verification'); }});
                }
            })();''')
            page = await context.new_page()
            page.on('pageerror', lambda error: page_errors.append(str(error)[:500]))
            page.on('dialog', lambda dialog: dialog.dismiss())

            async def check(name, action):
                try:
                    detail = await action()
                    receipt['checks'].append({'name': name, 'status': 'passed', 'detail': detail or ''})
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    receipt['checks'].append({'name': name, 'status': 'failed', 'detail': str(exc)[:600]})

            response = await page.goto(url, wait_until='domcontentloaded')
            if response is None or response.status != 200:
                raise RuntimeError('Preview root did not return HTTP 200')

            async def render():
                await page.locator('#organization-tree button[data-employee-id]').first.wait_for(state='visible')
                await page.locator('#reconnect.connected').wait_for(state='visible')
                if await page.locator('#page-error').is_visible():
                    raise ValueError(await page.locator('#page-error').inner_text())
                count = await page.locator('#organization-tree button[data-employee-id]').count()
                if not (await page.locator('#employee-name').inner_text()).strip():
                    raise ValueError('Employee name is empty')
                return f'{count} staff cards rendered from preview organization data'

            async def images():
                await page.wait_for_function('Array.from(document.images).every(image => image.complete)')
                broken = await page.locator('img').evaluate_all('(images) => images.filter(i => !i.naturalWidth).map(i => i.getAttribute("src"))')
                if broken:
                    raise ValueError('Images did not load: ' + ', '.join(broken))
                return f'{await page.locator("img").count()} images loaded (if present)'

            async def selection():
                cards = page.locator('#organization-tree button[data-employee-id]')
                if await cards.count() < 2:
                    raise ValueError('Need at least two employees to verify switching')
                card = cards.nth(1)
                expected_id = await card.get_attribute('data-employee-id')
                expected_name = await card.locator('.node-name').inner_text()
                await card.click()
                await page.wait_for_function('(id) => document.querySelector("#employee-panel").dataset.person === id', arg=expected_id)
                if await page.locator('#employee-name').inner_text() != expected_name:
                    raise ValueError('Selected employee detail does not match the card')
                return expected_name

            async def tabs():
                for name in ('memory', 'performance', 'work'):
                    await page.locator('#tab-' + name).click()
                    await page.wait_for_function('(name) => document.querySelector("#tab-" + name).getAttribute("aria-selected") === "true" && document.querySelector("#employee-panel").getAttribute("aria-labelledby") === "tab-" + name', arg=name)
                    if not (await page.locator('#employee-panel').inner_text()).strip():
                        raise ValueError('Employee ' + name + ' panel is empty')
                return '현재 업무 · 기억 · 성과 tabs respond without saving any data'

            async def layout(width, height, label):
                await page.set_viewport_size({'width': width, 'height': height})
                await page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                size = await page.evaluate('({width: innerWidth, content: document.documentElement.scrollWidth})')
                target = output_dir / (label + '.png')
                if target.is_symlink() or target.exists():
                    raise ValueError('Browser evidence file already exists: ' + target.name)
                await page.screenshot(path=str(target), full_page=True, timeout=5000)
                receipt['screenshots'].append(str(target))
                if size['content'] > size['width'] + 1:
                    raise ValueError(f'{label} horizontal overflow: {size}')
                return f'{width} × {height}: no document horizontal overflow'

            async def keyboard(width, height, label):
                await page.set_viewport_size({'width': width, 'height': height})
                # Reload resets the browser's native tab sequence for each size.
                # Do not synthesize focus: the first real Tab must reach the link.
                response = await page.goto(url, wait_until='domcontentloaded')
                if response is None or response.status != 200:
                    raise ValueError('Keyboard preview did not return HTTP 200')
                await render()
                detail = await _keyboard_skip_link(page, output_dir, receipt, label)
                return f'{width} × {height}; ' + detail

            async def mission_detail():
                await page.locator('#mission-list .mission-row').first.click()
                await page.locator('#mission-dialog[open] #detail-title').wait_for(state='visible')
                if not (await page.locator('#detail-title').inner_text()).strip():
                    raise ValueError('Mission title is empty')
                await page.locator('#close-mission').click()
                await page.locator('#mission-dialog').wait_for(state='hidden')
                return 'Opened and closed existing mission details without an action'

            await check('organization_render', render)
            await check('images', images)
            await check('keyboard_desktop', lambda: keyboard(1440, 1000, 'desktop'))
            await check('employee_selection', selection)
            await check('employee_tabs', tabs)
            await check('desktop_layout', lambda: layout(1440, 1000, 'desktop'))
            await check('keyboard_mobile', lambda: keyboard(390, 844, 'mobile'))
            await check('mobile_layout', lambda: layout(390, 844, 'mobile'))
            if await page.locator('#mission-list .mission-row').count():
                await check('mission_detail', mission_detail)
            else:
                receipt['checks'].append({'name': 'mission_detail', 'status': 'skipped', 'detail': 'No existing mission in preview data'})
            receipt['checks'].append({'name': 'network_boundary', 'status': 'failed' if blocked else 'passed', 'detail': blocked})
            receipt['checks'].append({'name': 'browser_errors', 'status': 'failed' if page_errors or http_errors else 'passed', 'detail': page_errors + http_errors})
            receipt['errors'].extend(page_errors + http_errors + blocked)
        finally:
            if browser is not None:
                try:
                    await asyncio.wait_for(browser.close(), timeout=4)
                except (Exception, asyncio.CancelledError):
                    pass
            try:
                await asyncio.wait_for(playwright.stop(), timeout=3)
            except (Exception, asyncio.CancelledError):
                pass

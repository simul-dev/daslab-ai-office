"""Scoped UI workspaces and isolated read-only loopback previews."""
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import subprocess
import threading
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

REQUIRED = ('office.html', 'office.css', 'office.js')
ASSETS = {'.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.ico', '.woff2'}
PREVIEW_JS = r'''"use strict";
(() => {
 const deny = '#command-form, #assign-selected, .memory-form, .detail-actions, .detail-intervention, #reconnect, .runtime-check';
 const lock = () => {
  document.querySelectorAll(deny).forEach(e => { e.querySelectorAll('button,input,textarea,select').forEach(x => { if (!x.disabled) x.disabled = true; }); if (e.matches('button') && !e.disabled) e.disabled = true; });
  document.querySelectorAll('a[href^="/voice"],a[href="/legacy"]').forEach(e => { e.removeAttribute('href'); e.setAttribute('aria-disabled','true'); });
 };
 document.addEventListener('submit', e => { e.preventDefault(); e.stopImmediatePropagation(); }, true);
 document.addEventListener('click', e => { if (e.target.closest(deny)) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
 new MutationObserver(lock).observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']}); lock();
})();'''


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _safe(path, boundary):
    path, boundary = Path(os.path.abspath(path)), Path(os.path.abspath(boundary))
    if not path.is_relative_to(boundary):
        raise ValueError('Workspace path escaped its allowed directory')
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Symbolic links and reparse points are not allowed')
    return path


class _HTML(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids, self.scripts, self.styles = set(), [], []
        self.unsafe = False
        self.body_count = 0
        self.feed(source)

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if tag == 'body':
            self.body_count += 1
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        if tag == 'script':
            self.scripts.append(attrs.get('src', ''))
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.styles.append(attrs.get('href', ''))
        if tag in ('iframe', 'object', 'embed', 'base') or any(k.startswith('on') for k in attrs):
            self.unsafe = True
        if tag == 'meta' and attrs.get('http-equiv', '').lower() == 'refresh':
            self.unsafe = True


class DevelopmentWorkspace:
    def __init__(self, root, data_dir):
        self.root, self.data_dir = Path(root).absolute(), Path(data_dir).absolute()
        self.lock = threading.RLock()
        self.servers = {}

    def _folder(self, folder):
        return _safe(folder, self.data_dir)

    def _files(self, static):
        _safe(static, static)
        result = {}
        for base, dirs, files in os.walk(static, followlinks=False):
            for name in dirs:
                _safe(Path(base) / name, static)
            for name in files:
                path = _safe(Path(base) / name, static)
                relative = path.relative_to(static).as_posix()
                if relative not in REQUIRED and not (relative.startswith('brand/') and path.suffix.lower() in ASSETS):
                    raise ValueError('Unexpected workspace file: ' + relative)
                if path.stat().st_size > 10_000_000 or len(result) >= 512:
                    raise ValueError('Preview asset limit exceeded')
                data = path.read_bytes()
                if path.suffix.lower() == '.svg':
                    svg = data.decode('utf-8')
                    if re.search(r'<\s*(script|foreignObject|iframe)|\bon\w+\s*=|(?:href|src)\s*=\s*[\"\']\s*(?:https?:|//|data:|javascript:)|<!ENTITY|<!DOCTYPE', svg, re.I):
                        raise ValueError('Unsafe SVG asset')
                result[relative] = data
        for name in REQUIRED:
            if name not in result:
                raise ValueError('Missing required file: ' + name)
        return result

    def prepare(self, folder, previous_folder=None):
        folder = self._folder(folder)
        workspace = _safe(folder / 'workspace', folder)
        if workspace.exists():
            raise ValueError('Development workspace already exists')
        source = self.root / 'static'
        if previous_folder is not None:
            previous_folder = self._folder(previous_folder)
            if not self.finish(previous_folder)['ready']:
                raise ValueError('Previous workspace has not passed verification')
            source = previous_folder / 'workspace/static'
        files = {}
        for name in REQUIRED:
            path = _safe(source / name, source)
            files[name] = path.read_bytes()
        brand = _safe(source / 'brand', source)
        if brand.exists():
            for base, dirs, names in os.walk(brand, followlinks=False):
                for name in dirs:
                    _safe(Path(base) / name, source)
                for name in names:
                    path = _safe(Path(base) / name, source)
                    if path.suffix.lower() not in ASSETS:
                        raise ValueError('Unsupported brand asset')
                    files[path.relative_to(source).as_posix()] = path.read_bytes()
        for name, data in files.items():
            target = workspace / 'static' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self._files(workspace / 'static')
        brand_context = _safe(self.root / 'knowledge/office-brand.md', self.root)
        if brand_context.is_file():
            (workspace / 'BRAND.md').write_text(brand_context.read_text(encoding='utf-8'), encoding='utf-8')
        manifest = {'files': {name: _hash(data) for name, data in files.items()}, 'html': files['office.html'].decode('utf-8')}
        (folder / 'development-baseline.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        return {'workspace': str(workspace), 'static_dir': str(workspace / 'static'), 'files': list(files), 'target': 'DAS Lab internal organization operations UI', 'preview_read_only': True}

    def finish(self, folder):
        checks, changed = [], []
        try:
            folder = self._folder(folder)
            baseline = json.loads(_safe(folder / 'development-baseline.json', folder).read_text(encoding='utf-8'))
            files = self._files(folder / 'workspace/static')
            before, after = _HTML(baseline['html']), _HTML(files['office.html'].decode('utf-8'))
            if not before.ids <= after.ids:
                raise ValueError('Required UI identifiers were removed')
            if after.body_count != 1 or not re.search(r'</body\s*>', files['office.html'].decode('utf-8'), re.I):
                raise ValueError('Preview requires one complete HTML body')
            if after.unsafe or after.scripts != before.scripts or after.styles != before.styles or any(s not in ('/office.js', 'office.js') for s in after.scripts):
                raise ValueError('Required scripts/styles changed or unsafe HTML introduced')
            checks.append('Required DOM IDs and local script/style references preserved')
            node = shutil.which('node')
            if not node:
                raise ValueError('Node.js unavailable: syntax verification cannot run')
            process = subprocess.run([node, '--check', str(folder / 'workspace/static/office.js')], capture_output=True, text=True, timeout=20, shell=False)
            if process.returncode:
                raise ValueError('JavaScript syntax check failed: ' + process.stderr[:800])
            checks.append('JavaScript syntax verified with node --check')
            hashes = {name: _hash(data) for name, data in files.items()}
            changed = sorted(name for name, digest in hashes.items() if baseline['files'].get(name) != digest)
            changed += sorted(set(baseline['files']) - set(hashes))
            if not set(changed) & {'office.html', 'office.css'}:
                raise ValueError('No actual HTML or style change was produced')
            checks.append('Actual HTML/style changes detected; asset scope checked')
            return {'ready': True, 'changed_files': changed, 'checks': checks, 'artifacts': hashes, 'visual_verified': False, 'applied_to_live': False}
        except (ValueError, OSError, UnicodeError, KeyError, subprocess.SubprocessError) as exc:
            return {'ready': False, 'changed_files': changed, 'checks': checks, 'error': str(exc), 'visual_verified': False, 'applied_to_live': False}

    def open_preview(self, folder, snapshot_provider, expected_artifacts=None):
        with self.lock:
            folder = self._folder(folder)
            key = str(folder)
            if key in self.servers:
                return self.servers[key][2]
            result = self.finish(folder)
            if not result['ready']:
                raise ValueError(result.get('error', 'Preview verification failed'))
            files = self._files(folder / 'workspace/static')
            if expected_artifacts is not None and {name: _hash(data) for name, data in files.items()} != expected_artifacts:
                raise ValueError('Preview files no longer match the verified delivery')
            html = files['office.html'].decode('utf-8')
            banner = '<div role="status" id="development-preview-banner">디자인 미리보기 · 실제 조직 데이터 · 읽기 전용 · 운영 화면에 아직 적용하지 않음</div>'
            html = re.sub(r'(<body\b[^>]*>)', lambda m: m[1] + banner, html, count=1, flags=re.I)
            html = re.sub(r'</body\s*>', '<script src="/preview-mode.js"></script></body>', html, count=1, flags=re.I)
            files['office.html'] = html.encode('utf-8')
            files['office.css'] += b'\n#development-preview-banner{position:relative;z-index:9999;min-height:44px;padding:12px 20px;background:#153d58;color:white;font:600 14px/20px sans-serif;text-align:center} @media(min-width:761px){.sidebar{top:44px}} [aria-disabled="true"]{cursor:not-allowed;opacity:.5}'
            files['preview-mode.js'] = PREVIEW_JS.encode('utf-8')
            stop = threading.Event()
            slots = threading.BoundedSemaphore(8)

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def respond(self, status, body, content_type='application/json; charset=utf-8'):
                    self.send_response(status)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Referrer-Policy', 'no-referrer')
                    self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                    self.end_headers()
                    self.wfile.write(body)

                def do_POST(self):
                    self.respond(405, b'{"error":"Read-only design preview"}')

                do_PUT = do_DELETE = do_PATCH = do_POST

                def do_GET(self):
                    port = self.server.server_address[1]
                    if self.headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
                        self.respond(403, b'{"error":"Invalid host"}')
                        return
                    path = unquote(urlsplit(self.path).path)
                    if '\\' in path or any(p in ('.', '..') for p in path.split('/')):
                        self.respond(404, b'{}')
                        return
                    if path == '/api/org/events':
                        if not slots.acquire(False):
                            self.respond(503, b'{}')
                            return
                        try:
                            self.send_response(200)
                            self.send_header('Content-Type', 'text/event-stream')
                            self.send_header('Cache-Control', 'no-store')
                            self.end_headers()
                            while not stop.is_set():
                                snapshot = snapshot_provider()
                                data = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))
                                self.wfile.write(('event: snapshot\ndata: ' + data + '\n\n').encode('utf-8'))
                                self.wfile.flush()
                                if stop.wait(5):
                                    break
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        finally:
                            slots.release()
                        return
                    if path == '/api/org':
                        self.respond(200, json.dumps(snapshot_provider(), ensure_ascii=False).encode('utf-8'))
                        return
                    match = re.fullmatch(r'/api/org/missions/([a-zA-Z0-9_-]+)', path)
                    if match:
                        mission = next((m for m in snapshot_provider().get('missions', []) if m['id'] == match[1]), None)
                        self.respond(200 if mission else 404, json.dumps({'mission': mission, 'events': [], 'attempts': [], 'preview_note': 'Preview shows current mission summary; full history is in the operations app'}, ensure_ascii=False).encode('utf-8'))
                        return
                    name = 'office.html' if path in ('/', '/index.html') else path.lstrip('/')
                    if name not in files:
                        self.respond(404, b'{}')
                        return
                    mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
                    if name.endswith('.js'):
                        mime = 'text/javascript'
                    self.respond(200, files[name], mime + ('; charset=utf-8' if name.endswith(('.html', '.css', '.js', '.svg')) else ''))

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f'http://127.0.0.1:{server.server_address[1]}/'
            self.servers[key] = (server, stop, url, thread)
            return url

    def close(self):
        with self.lock:
            for server, stop, _, thread in self.servers.values():
                stop.set()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            self.servers.clear()

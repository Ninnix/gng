import base64
import html
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree


DEFAULT_PORT = 9333
CONNECT_TIMEOUT = 3
CDP_TIMEOUT = 20
MAX_RESULT_BYTES = 1024 * 1024


class WebError(RuntimeError):
    pass


class WebSocket:
    def __init__(self, url: str, timeout: int = CDP_TIMEOUT) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "ws" or not parsed.hostname:
            raise WebError(f"unsupported websocket URL: {url}")
        self.sock = socket.create_connection((parsed.hostname, parsed.port or 80), CONNECT_TIMEOUT)
        self.sock.settimeout(timeout)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port or 80}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.sock.sendall(request)
        response = self._read_http_head()
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            self.close()
            raise WebError("websocket handshake rejected")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xffff:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = secrets.token_bytes(4)
        header.extend(mask)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.sendall(header + masked)

    def read_text(self) -> str:
        parts = []
        while True:
            head = self._read_exact(2)
            fin = head[0] & 0x80
            opcode = head[0] & 0x0f
            masked = head[1] & 0x80
            length = head[1] & 0x7f
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            if length > MAX_RESULT_BYTES * 4:
                raise WebError("websocket message too large")
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length) if length else b""
            if masked:
                payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise WebError("websocket closed")
            if opcode == 0x9:
                self._send_pong(payload[:125])
                continue
            if opcode in (0x0, 0x1):
                parts.append(payload)
                if fin:
                    return b"".join(parts).decode("utf-8", "replace")

    def _read_http_head(self) -> bytes:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 8192:
                break
        return bytes(data)

    def _read_exact(self, length: int) -> bytes:
        data = bytearray()
        while len(data) < length:
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                raise WebError("websocket frame read failed")
            data.extend(chunk)
        return bytes(data)

    def _send_pong(self, payload: bytes) -> None:
        mask = secrets.token_bytes(4)
        frame = bytearray([0x8a, 0x80 | len(payload)])
        frame.extend(mask)
        frame.extend(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.sendall(frame)


class Web:
    def __init__(self, port: int = DEFAULT_PORT, home: str | None = None) -> None:
        self.port = int(os.getenv("PROTOGQ_CDP_PORT", port))
        self.host = os.getenv("PROTOGQ_CDP_HOST", "127.0.0.1")
        self.home = Path(home or os.getenv("HOME") or ".")
        self.profile_dir = self.home / ".protogq" / "browser"
        self.chrome_process: subprocess.Popen | None = None
        self.next_id = 1

    def google_search(self, query: str) -> str:
        if not query:
            raise WebError("google_search requires query")
        url = "https://www.google.com/search?q=" + quote(query)
        try:
            return self._run_page_js(url, SEARCH_JS, dynamic_scroll=False)
        except (OSError, TimeoutError, WebError, json.JSONDecodeError):
            return self._google_news_search(query)

    def visit_page(self, url: str) -> str:
        if not url:
            raise WebError("visit_page requires url")
        return self._run_page_js(url, PAGE_JS, dynamic_scroll=True)

    def _google_news_search(self, query: str) -> str:
        url = "https://news.google.com/rss/search?q=" + quote(query, safe="")
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=CONNECT_TIMEOUT * 4) as response:
            data = response.read(MAX_RESULT_BYTES)
        root = ElementTree.fromstring(data)
        lines = ["# Google News search results", "", f"Query: {query}", ""]
        for item in root.findall(".//item")[:10]:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            source = item.findtext("source") or ""
            pub_date = item.findtext("pubDate") or ""
            description = self._clean_html(item.findtext("description") or "")
            if not title:
                continue
            lines.append(f"- [{title}]({link})")
            if source or pub_date:
                lines.append(f"  Source: {source} {pub_date}".strip())
            if description:
                lines.append("  " + description[:400])
        return "\n".join(lines)

    @staticmethod
    def _clean_html(text: str) -> str:
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    def _run_page_js(self, url: str, js: str, dynamic_scroll: bool) -> str:
        self._ensure_browser()
        target_id, ws_url = self._open_tab("about:blank")
        ws = WebSocket(ws_url)
        try:
            self._prepare_page(ws)
            self._navigate(ws, url)
            self._wait_navigated_ready(ws)
            clicked = self._eval_string(ws, GOOGLE_CONSENT_JS)
            if clicked:
                time.sleep(1.5)
                self._wait_navigated_ready(ws)
            if dynamic_scroll:
                self._scroll_dynamic_page(ws)
            return self._eval_string(ws, js)
        finally:
            ws.close()
            self._close_tab(target_id)

    def _ensure_browser(self) -> None:
        if self._cdp_alive():
            return
        self._spawn_chrome()
        deadline = time.monotonic() + CONNECT_TIMEOUT * 8
        while time.monotonic() < deadline:
            if self._cdp_alive():
                return
            if self.chrome_process and self.chrome_process.poll() is not None:
                raise WebError("Chrome exited before CDP became ready")
            time.sleep(0.25)
        raise WebError(f"Chrome did not expose CDP on port {self.port}")

    def _spawn_chrome(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        executable = self._chrome_executable()
        if self.host == "127.0.0.1" and self._is_windows_executable(executable):
            self.host = self._wsl_host_ip() or self.host
        port_arg = f"--remote-debugging-port={self.port}"
        profile_arg = f"--user-data-dir={self._profile_dir_arg(executable)}"
        args = [
            executable,
            port_arg,
            "--remote-debugging-address=0.0.0.0",
            "--remote-allow-origins=*",
            profile_arg,
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--password-store=basic",
            "--mute-audio",
            "about:blank",
        ]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            args.insert(-1, "--no-sandbox")
        try:
            self.chrome_process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            raise WebError(f"failed to start Chrome: {e}") from e

    @staticmethod
    def _chrome_executable() -> str:
        env = os.getenv("PROTOGQ_CHROME")
        if env:
            return env
        candidates = []
        if sys.platform == "darwin":
            candidates.extend([
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            ])
        if sys.platform.startswith("win"):
            for root in (os.getenv("PROGRAMFILES"), os.getenv("PROGRAMFILES(X86)"), os.getenv("LOCALAPPDATA")):
                if root:
                    candidates.append(str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"))
        candidates.extend([
            "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
            "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        ])
        candidates.extend([
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
            "/opt/google/chrome/chrome",
        ])
        for candidate in candidates:
            if Path(candidate).exists():
                return candidate
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                return found
        return "google-chrome"

    @staticmethod
    def _is_windows_executable(executable: str) -> bool:
        lower = executable.lower()
        return lower.endswith(".exe") or lower.startswith("/mnt/c/")

    @staticmethod
    def _wsl_host_ip() -> str:
        try:
            with open("/etc/resolv.conf", encoding="utf-8") as fp:
                for line in fp:
                    parts = line.split()
                    if len(parts) == 2 and parts[0] == "nameserver":
                        return parts[1]
        except OSError:
            pass
        return ""

    def _profile_dir_arg(self, executable: str) -> str:
        profile = os.getenv("PROTOGQ_CHROME_PROFILE")
        if profile:
            return profile
        if self._is_windows_executable(executable):
            try:
                temp = subprocess.check_output(["cmd.exe", "/c", "echo %TEMP%"], text=True).strip()
                if temp:
                    return temp + "\\protogq-browser"
            except (OSError, subprocess.SubprocessError):
                pass
        return str(self.profile_dir)

    def _cdp_alive(self) -> bool:
        try:
            body = self._http_request("GET", "/json/version")
        except OSError:
            return False
        return "webSocketDebuggerUrl" in body

    def _browser_ws_url(self) -> str:
        body = self._http_request("GET", "/json/version")
        data = json.loads(body)
        ws_url = data.get("webSocketDebuggerUrl")
        if not ws_url:
            raise WebError("Chrome did not return a browser WebSocket URL")
        return self._ws_url(ws_url)

    def _open_tab(self, url: str) -> tuple[str, str]:
        browser = WebSocket(self._browser_ws_url())
        try:
            response = self._cdp_call(browser, "Target.createTarget", {
                "url": url,
                "background": True,
                "newWindow": False,
            })
        finally:
            browser.close()
        target_id = response.get("result", {}).get("targetId")
        if not target_id:
            raise WebError("Chrome did not return a page target id")
        return target_id, self._ws_url(f"ws://127.0.0.1:{self.port}/devtools/page/{target_id}")

    def _ws_url(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        return f"ws://{self.host}:{parsed.port or self.port}{path}"

    def _close_tab(self, target_id: str) -> None:
        try:
            self._http_request("GET", "/json/close/" + quote(target_id, safe=""))
        except OSError:
            pass

    def _prepare_page(self, ws: WebSocket) -> None:
        self._cdp_call(ws, "Page.enable", {})
        self._cdp_call(ws, "Runtime.enable", {})
        self._cdp_call(ws, "Emulation.setFocusEmulationEnabled", {"enabled": True}, optional=True)
        self._cdp_call(ws, "Emulation.setDeviceMetricsOverride", {
            "width": 1365,
            "height": 900,
            "deviceScaleFactor": 1,
            "mobile": False,
        }, optional=True)
        self._wait_ready(ws)

    def _navigate(self, ws: WebSocket, url: str) -> None:
        self._cdp_call(ws, "Page.navigate", {"url": url})

    def _wait_ready(self, ws: WebSocket) -> None:
        for _ in range(80):
            state = self._eval_string(ws, "document.readyState", optional=True)
            if state in ("complete", "interactive"):
                time.sleep(0.8)
                return
            time.sleep(0.25)

    def _wait_navigated_ready(self, ws: WebSocket) -> None:
        last_len = -1
        stable = 0
        saw_real_url = False
        for i in range(100):
            probe = self._eval_string(ws, PROBE_JS, optional=True)
            if not probe:
                time.sleep(0.25)
                continue
            parts = probe.split("\n", 2)
            if len(parts) != 3:
                time.sleep(0.25)
                continue
            href, ready, text_len_text = parts
            try:
                text_len = int(text_len_text)
            except ValueError:
                text_len = 0
            real_url = href and href != "about:blank" and not href.startswith("chrome://")
            if real_url:
                saw_real_url = True
            ready_state = ready in ("complete", "interactive")
            if text_len > 0 and text_len == last_len:
                stable += 1
            else:
                stable = 0
            last_len = text_len
            if saw_real_url and ready_state and text_len > 0 and stable >= 2:
                time.sleep(0.5)
                return
            if saw_real_url and ready_state and i >= 24:
                return
            time.sleep(0.25)

    def _scroll_dynamic_page(self, ws: WebSocket) -> None:
        self._eval_string(ws, SCROLL_JS, optional=True)

    def _eval_string(self, ws: WebSocket, expression: str, optional: bool = False) -> str:
        response = self._cdp_call(ws, "Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
            "includeCommandLineAPI": True,
        }, optional=optional)
        if not response:
            return ""
        if "exceptionDetails" in response:
            if optional:
                return ""
            raise WebError("JavaScript evaluation failed")
        value = response.get("result", {}).get("result", {}).get("value")
        if value is None:
            return ""
        return str(value)

    def _cdp_call(self, ws: WebSocket, method: str, params: dict,
                  optional: bool = False) -> dict | None:
        call_id = self.next_id
        self.next_id += 1
        ws.send_text(json.dumps({"id": call_id, "method": method, "params": params}, separators=(",", ":")))
        while True:
            try:
                message = json.loads(ws.read_text())
            except (OSError, TimeoutError, WebError, json.JSONDecodeError):
                if optional:
                    return None
                raise
            if message.get("id") == call_id:
                if "error" in message and not optional:
                    raise WebError(message["error"].get("message", "CDP call failed"))
                return message

    def _http_request(self, method: str, path: str) -> str:
        with socket.create_connection((self.host, self.port), CONNECT_TIMEOUT) as sock:
            sock.settimeout(CONNECT_TIMEOUT)
            request = (
                f"{method} {path} HTTP/1.1\r\n"
                f"Host: {self.host}:{self.port}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            sock.sendall(request)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        response = b"".join(chunks)
        head, _, body = response.partition(b"\r\n\r\n")
        if not body:
            raise WebError("malformed HTTP response")
        if b"Transfer-Encoding: chunked" in head or b"transfer-encoding: chunked" in head:
            body = self._decode_chunked(body)
        return body.decode("utf-8", "replace")

    @staticmethod
    def _decode_chunked(body: bytes) -> bytes:
        out = bytearray()
        pos = 0
        while True:
            end = body.find(b"\r\n", pos)
            if end == -1:
                break
            size_text = body[pos:end].split(b";", 1)[0]
            try:
                size = int(size_text, 16)
            except ValueError:
                break
            pos = end + 2
            if size == 0:
                break
            out.extend(body[pos:pos + size])
            pos += size + 2
        return bytes(out)


GOOGLE_CONSENT_JS = r"""
(() => {
const clean=s=>(s||'').replace(/\s+/g,' ').trim();
const pats=[/accept all/i,/i agree/i,/agree/i,/accetta tutto/i,/tout accepter/i,/aceptar todo/i,/alle akzeptieren/i];
const els=[...document.querySelectorAll('button,[role=button],input[type=submit],a')];
for (const el of els){const t=clean(el.innerText||el.value||el.textContent);
if(!t)continue; if(pats.some(p=>p.test(t))){el.click(); return 'clicked '+t;}}
return '';
})()
""".strip()

PROBE_JS = r"""
location.href+'\n'+document.readyState+'\n'+((document.body&&document.body.innerText)||'').length
""".strip()

SEARCH_JS = r"""
(() => {
const clean=s=>(s||'').replace(/\s+/g,' ').trim();
const esc=s=>clean(s).replace(/\\/g,'\\\\').replace(/\[/g,'\\[').replace(/\]/g,'\\]').replace(/\n/g,' ');
const visible=el=>{const r=el.getBoundingClientRect();const st=getComputedStyle(el);return r.width>0&&r.height>0&&st.display!=='none'&&st.visibility!=='hidden'&&st.opacity!=='0';};
const bad=h=>(/(^|\.)google\./.test(h)||/(^|\.)gstatic\./.test(h)||/(^|\.)googleusercontent\./.test(h));
const lines=['# Google search results','',`URL: ${location.href}`,'','## Visible links'];
const seen=new Set();
for(const a of document.querySelectorAll('a[href]')){if(!visible(a))continue;let href=a.href||'';
try{const u=new URL(href);if(u.pathname==='/url'&&u.searchParams.get('q'))href=u.searchParams.get('q');}catch{}
let u;try{u=new URL(href);}catch{continue;}if(!/^https?:$/.test(u.protocol))continue;if(bad(u.hostname))continue;
const text=esc(a.innerText||a.textContent);if(text.length<3)continue;if(seen.has(u.href))continue;seen.add(u.href);
lines.push(`- [${text.slice(0,180)}](${u.href})`);if(seen.size>=20)break;}
lines.push('','## Text snapshot',clean(document.body.innerText).slice(0,1200));
return lines.join('\n');
})()
""".strip()

PAGE_JS = r"""
(() => {
const clean=s=>(s||'').replace(/\s+/g,' ').trim();
const esc=s=>clean(s).replace(/\\/g,'\\\\').replace(/\[/g,'\\[').replace(/\]/g,'\\]').replace(/\n/g,' ');
const visible=el=>{const r=el.getBoundingClientRect();const st=getComputedStyle(el);return r.width>0&&r.height>0&&st.display!=='none'&&st.visibility!=='hidden'&&st.opacity!=='0';};
const inline=n=>{if(!n)return'';if(n.nodeType===3)return n.nodeValue;if(n.nodeType!==1)return'';const el=n;
if(el.tagName==='SCRIPT'||el.tagName==='STYLE'||el.tagName==='NOSCRIPT')return'';
if(el.tagName==='A'){const t=esc(el.innerText||el.textContent);const h=el.href||'';return t&&h?`[${t}](${h})`:t;}
if(el.tagName==='CODE')return '`'+clean(el.innerText||el.textContent).replace(/`/g,'\\`')+'`';
return [...el.childNodes].map(inline).join('');};
const lines=[`# ${clean(document.title)||location.href}`,'',`URL: ${location.href}`,'','## Content'];
const blocks=[...document.body.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,pre,blockquote,td,th,[id="content-text"],[class*="comment-body"],[class*="comment-content"],[data-testid*="comment-text"]')];
const seen=new Set();
for(const el of blocks){if(!visible(el))continue;let s='';const tag=el.tagName;
if(/^H[1-6]$/.test(tag)){s='#'.repeat(Number(tag[1]))+' '+inline(el);}
else if(tag==='LI'){s='- '+inline(el);}
else if(tag==='PRE'){s='```\n'+(el.innerText||el.textContent||'').trimEnd()+'\n```';}
else if(tag==='BLOCKQUOTE'){s='> '+clean(el.innerText||el.textContent);}
else{s=inline(el);}s=s.trim();if(!s||seen.has(s))continue;seen.add(s);lines.push('',s);
if(lines.join('\n').length>900000){lines.push('','[Content truncated by browser extractor.]');break;}}
lines.push('','## Visible links');let n=0;const linkSeen=new Set();
for(const a of document.querySelectorAll('a[href]')){if(!visible(a))continue;const t=esc(a.innerText||a.textContent);if(t.length<3)continue;
let u;try{u=new URL(a.href);}catch{continue;}if(!/^https?:$/.test(u.protocol)||linkSeen.has(u.href))continue;linkSeen.add(u.href);
lines.push(`- [${t.slice(0,160)}](${u.href})`);if(++n>=80)break;}
return lines.join('\n');
})()
""".strip()

SCROLL_JS = r"""
(() => new Promise(resolve => {
const root=()=>document.scrollingElement||document.documentElement||document.body;
const blockSel='h1,h2,h3,h4,h5,h6,p,li,pre,blockquote,td,th,[id="content-text"],[class*="comment-body"],[class*="comment-content"],[data-testid*="comment-text"]';
const lazySel='[onscroll],[loading="lazy"],[data-src],[data-lazy],[class*="lazy"],[class*="infinite"],[class*="virtual"],[role="feed"],[id*="comment"],[class*="comment"],[data-testid*="comment"]';
const hookCount=()=>{let n=0;try{if(window.onscroll)n++;if(document.onscroll)n++;if(document.body&&document.body.onscroll)n++;}catch(e){}
try{if(typeof getEventListeners==='function'){for(const o of [window,document,document.body]){if(!o)continue;const ev=getEventListeners(o);if(ev&&ev.scroll)n+=ev.scroll.length;}}}catch(e){}
try{n+=document.querySelectorAll(lazySel).length;}catch(e){}return n;};
const metrics=()=>{const r=root();return {height:r?r.scrollHeight:0,view:innerHeight||900,y:scrollY||(r&&r.scrollTop)||0,text:((document.body&&document.body.innerText)||'').length,links:document.links?document.links.length:0,blocks:document.body?document.body.querySelectorAll(blockSel).length:0,hooks:hookCount()};};
const sig=m=>[m.height,m.text,m.links,m.blocks].join('|');
const grew=(a,b)=>b.height>a.height+20||b.text>a.text+200||b.links>a.links+2||b.blocks>a.blocks+2;
const scrollOnce=()=>{const r=root();if(!r)return;const h=Math.max(700,Math.floor((innerHeight||900)*0.85));window.scrollTo(0,Math.min(r.scrollHeight,(scrollY||r.scrollTop||0)+h));};
let last=metrics(),lastSig=sig(last),same=0,steps=0;
const scrollable=last.height>last.view*1.35;
if(!scrollable||last.hooks===0){resolve('scroll skipped hooks='+last.hooks+' text='+last.text);return;}
const tick=()=>{if(steps>=28){resolve('scrolled '+steps+' text='+last.text);return;}
const before=last;scrollOnce();steps++;
setTimeout(()=>{const now=metrics(),nowSig=sig(now);if(nowSig===lastSig)same++;else same=0;
const loaded=grew(before,now);last=now;lastSig=nowSig;
if(steps===1&&!loaded){resolve('scroll probe unchanged text='+now.text);return;}
const atBottom=now.y+now.view+20>=now.height;
if(same>=4||(atBottom&&same>=1)){resolve('scrolled '+steps+' text='+now.text);return;}
tick();},900);};tick();
}))()
""".strip()

"""Credential-based login manager.

Drives the Huawei ID web login once (username + password + a one-time SMS code),
then marks the browser profile as trusted. After that it re-logs in silently with
only the username and password whenever the access token expires, so tools never
have to ask for a code again unless Huawei drops the trust.

The browser is used *only* to obtain the access token; data requests use httpx with
that token. A single persistent Playwright context lives for the server's lifetime;
its profile directory (the trust cookie) is persisted on a mounted volume.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from playwright.sync_api import Page, sync_playwright

TRAINING_CAMP_URL = "https://health.cloud.huawei.com/TrainingCamp"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)
TOKEN_REFRESH_MARGIN_S = 120  # refresh when under 2 min remain
CODE_PLACEHOLDER = re.compile("code", re.I)

# Maps Huawei's numeric "site" to the client.py region key.
SITE_TO_REGION = {1: "drcn", 5: "dra", 7: "dre"}


class LoginError(Exception):
    """Login could not complete without human input (SMS code or captcha)."""


class CaptchaRequired(LoginError):
    pass


class CodeRequired(LoginError):
    pass


@dataclass
class Token:
    value: str
    expires_at: float  # unix seconds
    region: str

    @property
    def valid(self) -> bool:
        return bool(self.value) and time.time() < self.expires_at - TOKEN_REFRESH_MARGIN_S


class LoginManager:
    """Thread-safe holder of the trusted browser context and current token."""

    def __init__(self, account: str, password: str, profile_dir: str, headless: bool = True):
        if not account or not password:
            raise ValueError("HUAWEI_ACCOUNT and HUAWEI_PASSWORD are required")
        self._account = account
        self._password = password
        self._profile_dir = profile_dir
        self._headless = headless
        # All Playwright work runs in this single thread (sync API is not
        # thread-safe, and FastMCP dispatches tools across a thread pool).
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hw-login")
        self._pw = None
        self._ctx = None
        self._token: Token | None = None
        self._pending_page: Page | None = None  # page parked at the verify modal

    def _run(self, fn, *args):
        return self._exec.submit(fn, *args).result()

    # --- browser lifecycle --------------------------------------------------

    def _context(self):
        if self._ctx is None:
            self._pw = sync_playwright().start()
            self._ctx = self._pw.chromium.launch_persistent_context(
                self._profile_dir,
                headless=self._headless,
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
        return self._ctx

    def close(self) -> None:
        def _close():
            for obj in (self._ctx, self._pw):
                try:
                    obj and (obj.close() if self._ctx is obj else obj.stop())
                except Exception:
                    pass
            self._ctx = self._pw = self._pending_page = None
        try:
            self._run(_close)
        finally:
            self._exec.shutdown(wait=False)

    # --- page helpers -------------------------------------------------------

    @staticmethod
    def _click_exact(page: Page, text: str) -> bool:
        loc = page.get_by_text(text, exact=True)
        for i in range(loc.count()):
            b = loc.nth(i)
            try:
                if b.is_visible():
                    b.click()
                    return True
            except Exception:
                pass
        return False

    @staticmethod
    def _read_token(page: Page) -> Token | None:
        try:
            d = page.evaluate(
                "() => ({t: sessionStorage.getItem('accessToken'),"
                " e: sessionStorage.getItem('expireTime'),"
                " s: sessionStorage.getItem('site')})"
            )
        except Exception:
            return None
        if not d or not d.get("t"):
            return None
        expires_at = int(d["e"]) / 1000 if d.get("e") else time.time() + 3000
        region = SITE_TO_REGION.get(int(d["s"]), "dra") if d.get("s") else "dra"
        return Token(value=d["t"], expires_at=expires_at, region=region)

    def _captcha_visible(self, page: Page) -> bool:
        for sel in ("[class*=yidun]", "iframe[src*=captcha]", "#capaccount"):
            el = self._safe(lambda s=sel: page.query_selector(s))
            if el and self._safe(el.is_visible):
                return True
        return False

    @staticmethod
    def _safe(fn, default=None):
        """Run a page query that may race with a navigation."""
        try:
            return fn()
        except Exception:
            return default

    def _has_phone_field(self, page: Page) -> bool:
        return bool(self._safe(lambda: page.query_selector("input[placeholder*='Phone']")))

    def _open_login_form(self) -> Page:
        page = self._context().pages[0] if self._context().pages else self._context().new_page()
        page.goto(TRAINING_CAMP_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        tok = self._read_token(page)  # session may still be alive
        if tok and tok.valid:
            self._token = tok
            return page
        # Poll until a token appears (SSO still alive), the phone field appears,
        # or the landing "Log in" button renders so we can click it.
        clicked = False
        for _ in range(40):
            if self._read_token(page) or self._has_phone_field(page):
                break
            if not clicked:
                if btn := self._safe(lambda: page.query_selector("button.login-button")):
                    self._safe(btn.click)
                    clicked = True
            page.wait_for_timeout(1000)
        return page

    # --- public API ---------------------------------------------------------

    def status(self) -> dict:
        if self._token and self._token.valid:
            return {"logged_in": True, "expires_in_seconds": round(self._token.expires_at - time.time())}
        return {"logged_in": False, "awaiting_sms_code": self._pending_page is not None}

    def invalidate(self) -> None:
        """Drop the cached token so the next get_token refreshes."""
        self._token = None

    def get_token(self) -> Token:
        """Return a valid token, refreshing silently if needed.

        Raises CodeRequired if the trust was lost and a first-time SMS login is
        needed (the caller should run start_login), or CaptchaRequired on captcha.
        """
        def _impl() -> Token:
            if self._token and self._token.valid:
                return self._token
            return self._silent_login()
        return self._run(_impl)

    def _silent_login(self) -> Token:
        page = self._open_login_form()
        if self._token and self._token.valid:
            return self._token
        if tok := self._read_token(page):  # SSO completed on its own
            self._token = tok
            return tok
        if not self._has_phone_field(page):
            # token may land right after SSO redirect
            if tok := self._wait_token(page, 8):
                return tok
            raise LoginError("Unexpected login page state during silent login")
        page.fill("input[placeholder*='Phone']", self._account)
        page.fill("input[placeholder*='Password']", self._password)
        page.wait_for_timeout(400)
        self._click_exact(page, "LOG IN")
        # trusted device -> token directly; untrusted -> verify modal (needs code)
        for _ in range(15):
            page.wait_for_timeout(1500)
            if self._too_many_attempts(page):
                raise LoginError("Huawei is rate-limiting logins ('Too many attempts'); wait a while and retry")
            if self._captcha_visible(page):
                raise CaptchaRequired("Huawei requires a captcha; log in via browser once")
            if self._code_field_visible(page):
                # The verify modal does NOT auto-send; request the SMS explicitly.
                self._safe(lambda: self._click_exact(page, "Get code"))
                page.wait_for_timeout(1500)
                if self._captcha_visible(page):
                    raise CaptchaRequired("Huawei requires a captcha; log in via browser once")
                self._pending_page = page  # parked at verify modal
                raise CodeRequired("SMS verification required; call start_login/submit_sms_code")
            if tok := self._read_token(page):
                self._token = tok
                return tok
        raise LoginError("Silent login did not produce a token")

    def _too_many_attempts(self, page: Page) -> bool:
        body = self._safe(lambda: page.inner_text("body"), "") or ""
        return "Too many attempts" in body

    def _code_field_visible(self, page: Page) -> bool:
        def check():
            cf = page.get_by_placeholder(CODE_PLACEHOLDER)
            return bool(cf.count()) and cf.first.is_visible()
        return bool(self._safe(check, False))

    def start_login(self) -> dict:
        """Begin an interactive login. Sends the SMS and parks at the code prompt."""
        def _impl() -> dict:
            try:
                tok = self._silent_login()
                return {"status": "logged_in", "expires_in_seconds": round(tok.expires_at - time.time())}
            except CodeRequired:
                return {"status": "sms_sent", "message": "Enter the SMS code with submit_sms_code"}
            except CaptchaRequired as e:
                return {"status": "captcha_required", "message": str(e)}
        return self._run(_impl)

    def submit_sms_code(self, code: str) -> dict:
        def _impl() -> dict:
            page = self._pending_page
            if page is None:
                raise LoginError("No login in progress; call start_login first")
            cf = page.get_by_placeholder(CODE_PLACEHOLDER)
            cf.first.fill(code.strip())
            page.wait_for_timeout(500)
            if not self._click_exact(page, "OK"):
                cf.first.press("Enter")
            # "Trust this browser?" -> TRUST so future logins skip the code
            for _ in range(12):
                if self._click_exact(page, "TRUST"):
                    break
                page.wait_for_timeout(1500)
            tok = self._wait_token(page, 40)
            self._pending_page = None
            if not tok:
                raise LoginError("Code accepted but no token; the code may be wrong or expired")
            return {"status": "logged_in", "expires_in_seconds": round(tok.expires_at - time.time())}
        return self._run(_impl)

    def _wait_token(self, page: Page, tries: int) -> Token | None:
        for _ in range(tries):
            if tok := self._read_token(page):
                self._token = tok
                return tok
            page.wait_for_timeout(2000)
        return None

"""Exercise urllib redirect dispatch, form parsing, and cookie handling together."""

from email.message import Message
from http import HTTPStatus
from http.cookiejar import LWPCookieJar
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
from urllib.response import addinfourl
from zipfile import ZipFile

import prime_signin


EMAIL_URL = 'https://www.amazon.com/ap/signin'
PASSWORD_URL = 'https://www.amazon.com/ap/signin/password'
VERIFY_URL = 'https://www.amazon.com/ap/cvf/verify'
CONFIRM_URL = 'https://www.amazon.com/ap/cvf/confirm'
PRIME_RETURN = 'https://www.primevideo.com/auth-redirect?signin=0'
EMAIL = 'test-user@example.invalid'
PASSWORD = 'test-password-only'
CODE = '123456'

EMAIL_FORM = ('<form method="post" action="/ap/signin"><input name="email" type="email">'
              '<input type="hidden" name="state" value="email-state"></form>')
PASSWORD_FORM = ('<form method="post" action="/ap/signin/password">'
                 '<input name="password" type="password">'
                 '<input type="hidden" name="state" value="password-state"></form>')
CODE_FORM = ('<form id="fwcim-form" method="post" action="/ap/cvf/confirm">'
             '<input name="code" type="text" id="cvf-input-code">'
             '<input name="workflowState" type="hidden" value="verification-state">'
             '<button name="cvf_action" value="verify">Verify</button>'
             '<button name="cvf_action" value="resend">Resend</button></form>')
SIGNED_IN = '<span data-automation-id="nav-active-profile-name">Fixture viewer</span>'


class SignInFixture(HTTPSHandler):
    """Supply HTTP responses at the socket boundary of urllib's real opener."""

    def __init__(self, preserving_redirect, *, retry_code=False, destination_host='www.primevideo.com', approval=False):
        super().__init__()
        self.preserving_redirect = preserving_redirect
        self.retry_code = retry_code
        self.requests = []
        self.code_submissions = 0
        self.prime_return = 'https://' + destination_host + '/auth-redirect?signin=0'
        self.approval = approval

    def https_open(self, request):
        self.requests.append(request)
        url = request.full_url
        method = request.get_method()
        payload = parse_qs((request.data or b'').decode())
        if url == prime_signin.ACCOUNT:
            return self.response(request, 302, location=EMAIL_URL,
                                 cookie='pv-bootstrap=start; Secure; HttpOnly; Path=/')
        if url == EMAIL_URL and method == 'GET':
            return self.response(request, 200, EMAIL_FORM,
                                 cookie='signin-session=step-one; Secure; HttpOnly; Path=/ap/')
        if url == EMAIL_URL and method == 'POST':
            assert payload == {'email': [EMAIL], 'state': ['email-state']}, payload
            assert 'signin-session=step-one' in request.get_header('Cookie', '')
            return self.response(request, 200, PASSWORD_FORM)
        if url == PASSWORD_URL:
            assert method == 'POST'
            assert payload == {'password': [PASSWORD], 'state': ['password-state']}, payload
            target = VERIFY_URL if self.preserving_redirect in (307, 308) else self.prime_return
            return self.response(request, self.preserving_redirect, location=target)
        if url == VERIFY_URL:
            assert method == 'POST'
            assert payload == {'password': [PASSWORD], 'state': ['password-state']}, payload
            assert 'signin-session=step-one' in request.get_header('Cookie', '')
            form = CODE_FORM
            if self.approval:
                form = '<form id="pollingForm" method="post" action="/ap/cvf/confirm">' \
                       '<input type="hidden" name="transactionApprovalStatus" value="TransactionPending"></form>'
            return self.response(request, 200, form,
                                 cookie='signin-session=step-two; Secure; HttpOnly; Path=/ap/')
        if url == CONFIRM_URL:
            self.code_submissions += 1
            assert method == 'POST'
            expected = {'code': [CODE], 'workflowState': ['verification-state'], 'cvf_action': ['verify']}
            if self.approval:
                expected = {'transactionApprovalStatus': ['TransactionPending']}
            assert payload == expected, payload
            assert 'signin-session=step-two' in request.get_header('Cookie', '')
            if self.retry_code and self.code_submissions == 1:
                return self.response(request, 200, CODE_FORM)
            return self.response(request, 303, location=self.prime_return)
        if url == self.prime_return:
            assert method == 'GET'
            assert request.data is None
            assert 'signin-session' not in request.get_header('Cookie', '')
            if urlsplit(url).hostname == 'www.primevideo.com':
                assert 'pv-bootstrap=start' in request.get_header('Cookie', '')
            return self.response(request, 200, SIGNED_IN,
                                 cookie='at-main-av=signed-in-fixture; Secure; HttpOnly; Path=/')
        raise AssertionError(f'Unexpected request {method} {url}')

    @staticmethod
    def response(request, status, body='', *, location=None, cookie=None):
        headers = Message()
        headers['Content-Type'] = 'text/html; charset=utf-8'
        if location:
            headers['Location'] = location
        if cookie:
            headers['Set-Cookie'] = cookie
        response = addinfourl(BytesIO(body.encode()), headers, request.full_url, status)
        response.msg = HTTPStatus(status).phrase
        return response


def fixture_opener(fixture):
    def create(*handlers):
        return build_opener(ProxyHandler({}), fixture, *handlers)

    return create


class VerificationMethodFixture(SignInFixture):
    def __init__(self, approval_to_code=False):
        super().__init__(307, approval=approval_to_code)
        self.approval_to_code = approval_to_code
        self.method_submitted = False

    def https_open(self, request):
        payload = parse_qs((request.data or b'').decode())
        if request.full_url == VERIFY_URL and not self.approval_to_code:
            self.requests.append(request)
            html = ('<form id="auth-select-device-form" method="post" action="/ap/cvf/confirm">'
                    '<input type="hidden" name="workflowState" value="choose-state">'
                    '<label><input type="radio" name="otpDeviceContext" value="sms" checked>Text message</label>'
                    '<label for="email-device">Email a code</label>'
                    '<input id="email-device" type="radio" name="otpDeviceContext" value="email">'
                    '<input type="submit" name="sendCode" value="Send code"></form>')
            return self.response(request, 200, html,
                                 cookie='signin-session=step-two; Secure; HttpOnly; Path=/ap/')
        if request.full_url == CONFIRM_URL and 'otpDeviceContext' in payload:
            self.requests.append(request)
            assert payload == {'workflowState': ['choose-state'], 'otpDeviceContext': ['email'], 'sendCode': ['Send code']}
            self.method_submitted = True
            return self.response(request, 200, CODE_FORM)
        if request.full_url == CONFIRM_URL and self.approval_to_code and 'transactionApprovalStatus' in payload:
            self.requests.append(request)
            self.approval = False
            return self.response(request, 200, '<div id="auth-polling-container"></div>' + CODE_FORM)
        return super().https_open(request)


class SignInTransportTests(unittest.TestCase):
    def test_live_signin_country_picker_preserves_default_without_blocking_email_entry(self):
        html = ('<form name="register" method="post" action="/ap/register">'
                '<input type="text" name="customerName"><input type="email" name="email"></form>'
                '<form id="ap_login_form" name="signIn" method="post" action="/ap/signin">'
                '<input type="email" name="email" autocomplete="webauthn">'
                '<select name="countryCode"><option value="DE">Germany</option>'
                '<option value="US" selected>United States</option></select>'
                '<input id="ap-credential-autofill-hint" type="password" name="password"></form>')
        page = prime_signin.SignInDocument(html)
        self.assertTrue(page.forms[0].other_controls)
        self.assertFalse(page.forms[1].other_controls)
        self.assertEqual(page.forms[1].hidden, [('countryCode', 'US')])
        self.assertEqual(page.forms[1].prompts, [('email', 'Amazon email or mobile number', False)])

    def test_security_image_requires_manual_input_before_code_entry(self):
        captcha = ('<form id="fwcim-form" method="post" action="/ap/cvf/confirm">'
                   '<img alt="captcha" src="https://m.media-amazon.com/security-image.png">'
                   '<input type="text" name="cvf_captcha_input">'
                   '<input type="hidden" name="state" value="image-state">'
                   '<input type="submit" name="cvf_action" value="verifyCaptcha"></form>')
        pages = [(VERIFY_URL, prime_signin.SignInDocument(captcha)),
                 (CONFIRM_URL, prime_signin.SignInDocument(CODE_FORM)),
                 (PRIME_RETURN, prime_signin.SignInDocument(SIGNED_IN))]
        solve = Mock(return_value='manual-image-answer')
        with patch.object(prime_signin, 'request_document', side_effect=pages) as request, patch.object(
            prime_signin, 'read_security_image', return_value=(b'fixture-image', 'png')
        ), patch.object(prime_signin, 'has_authentication_cookie', return_value=True):
            self.assertTrue(prime_signin.authenticate(LWPCookieJar(), Mock(return_value=CODE), captcha=solve))
        solve.assert_called_once_with(b'fixture-image', 'png')
        fields = request.call_args_list[1].kwargs['fields']
        self.assertEqual(fields, [('state', 'image-state'), ('cvf_captcha_input', 'manual-image-answer'),
                                  ('cvf_action', 'verifyCaptcha')])

    def test_security_image_fetch_rejects_unrecognized_hosts(self):
        with patch.object(prime_signin, 'build_opener') as opener:
            with self.assertRaises(prime_signin.SignInError):
                prime_signin.read_security_image(LWPCookieJar(), 'https://outside.invalid/image.png')
            opener.assert_not_called()

    def test_verification_method_choice_precedes_email_code_entry(self):
        fixture = VerificationMethodFixture()
        choose = Mock(return_value=1)
        with patch.object(prime_signin, 'build_opener', side_effect=fixture_opener(fixture)):
            self.assertTrue(prime_signin.authenticate(LWPCookieJar(), Mock(side_effect=(EMAIL, PASSWORD, CODE)), choose=choose))
        choose.assert_called_once_with('Choose how to verify your Amazon sign-in', ['Text message', 'Email a code'])
        self.assertTrue(fixture.method_submitted)
        self.assertEqual(fixture.code_submissions, 1)

    def test_canceling_verification_method_does_not_send_a_code_request(self):
        fixture = VerificationMethodFixture()
        with patch.object(prime_signin, 'build_opener', side_effect=fixture_opener(fixture)):
            self.assertFalse(prime_signin.authenticate(LWPCookieJar(), Mock(side_effect=(EMAIL, PASSWORD)), choose=lambda *args: -1))
        self.assertFalse(fixture.method_submitted)
        self.assertEqual(fixture.code_submissions, 0)

    def test_approval_page_can_transition_to_a_code_form(self):
        fixture = VerificationMethodFixture(approval_to_code=True)
        approve = Mock(return_value=True)
        with patch.object(prime_signin, 'build_opener', side_effect=fixture_opener(fixture)):
            self.assertTrue(prime_signin.authenticate(LWPCookieJar(), Mock(side_effect=(EMAIL, PASSWORD, CODE)), approve=approve))
        approve.assert_called_once()
        self.assertEqual(fixture.code_submissions, 1)

    def test_approval_completion_status_outside_form_uses_server_return_link(self):
        page_url = 'https://www.amazon.com/ap/approval?openid.return_to=https%3A%2F%2Fwww.primevideo.com%2Fauth-redirect%3Fsignin%3D0'
        html = '<form id="pollingForm" method="post" action="/ap/approval"><input type="hidden" name="state" value="pending"></form>'
        complete = '<input type="hidden" name="transactionApprovalStatus" value="TransactionCompleted">'
        pages = [(page_url, prime_signin.SignInDocument(html)),
                 ('https://www.amazon.com/ap/approval', prime_signin.SignInDocument(complete)),
                 (PRIME_RETURN, prime_signin.SignInDocument(SIGNED_IN))]
        with patch.object(prime_signin, 'request_document', side_effect=pages) as read, patch.object(
            prime_signin, 'has_authentication_cookie', return_value=True
        ):
            self.assertTrue(prime_signin.authenticate(LWPCookieJar(), Mock(), approve=lambda message: True))
        self.assertEqual(read.call_args_list[-1].args[1], PRIME_RETURN)

    def test_native_email_approval_continues_in_the_same_cookie_session(self):
        fixture = SignInFixture(307, approval=True)
        approve = Mock(return_value=True)
        with patch.object(prime_signin, 'build_opener', side_effect=fixture_opener(fixture)):
            self.assertTrue(prime_signin.authenticate(LWPCookieJar(), Mock(side_effect=(EMAIL, PASSWORD)), approve=approve))
        approve.assert_called_once()
        self.assertEqual(fixture.code_submissions, 1)

    def test_session_is_confirmed_from_prime_video_request_context(self):
        html = '<script type="application/json">{"RequestContext":{"customerID":"fixture-customer"}}</script>'
        page = prime_signin.SignInDocument(html)
        self.assertTrue(page.authenticated_context)
        self.assertFalse(prime_signin.SignInDocument(html.replace('"fixture-customer"', 'null')).authenticated_context)
        cookies = LWPCookieJar()
        headers = Message()
        headers['Set-Cookie'] = 'at-main-av=fixture-auth; Secure; HttpOnly; Path=/'
        response = addinfourl(BytesIO(), headers, prime_signin.ACCOUNT, 200)
        cookies.extract_cookies(response, Request(prime_signin.ACCOUNT))
        with patch.object(prime_signin, 'request_document', return_value=(prime_signin.ACCOUNT, page)):
            with self.assertRaisesRegex(prime_signin.SignInError, 'complete sign-in session'):
                prime_signin.authenticate(LWPCookieJar(), Mock())
            self.assertTrue(prime_signin.authenticate(cookies, Mock()))

    def test_observed_north_american_redirect_after_password(self):
        fixture = SignInFixture(302, destination_host='na.primevideo.com')
        events = []
        with patch.object(prime_signin, 'build_opener', side_effect=fixture_opener(fixture)):
            self.assertTrue(prime_signin.authenticate(LWPCookieJar(), Mock(side_effect=(EMAIL, PASSWORD)), observer=events.append))
        self.assertIn({'event': 'redirect', 'status': 302, 'method': 'POST',
                       'from_host': 'www.amazon.com', 'to_host': 'na.primevideo.com',
                       'source': {'host': 'www.amazon.com', 'route': '/ap/signin/password',
                                  'query_keys': [], 'authorization_code_present': False},
                       'destination': {'host': 'na.primevideo.com', 'route': '/auth-redirect',
                                       'query_keys': ['signin'], 'authorization_code_present': False},
                       'preserves_submission': False}, events)
        self.assertEqual(fixture.requests[-1].get_method(), 'GET')
        self.assertIsNone(fixture.requests[-1].data)

    def test_complete_email_password_code_flow_with_post_redirects(self):
        for status in (307, 308):
            with self.subTest(status=status), TemporaryDirectory(dir=Path(__file__).parent) as directory:
                fixture = SignInFixture(status)
                cookie_file = Path(directory) / 'session.cookies'
                cookies = LWPCookieJar()
                prompt = Mock(side_effect=(EMAIL, PASSWORD, CODE))
                with patch.object(prime_signin, 'build_opener', side_effect=fixture_opener(fixture)):
                    self.assertTrue(prime_signin.authenticate(cookies, prompt))
                prime_signin.save_cookies(cookies, cookie_file)
                reloaded = prime_signin.load_cookies(cookie_file)
                self.assertEqual({item.name: item.value for item in reloaded}['at-main-av'], 'signed-in-fixture')
                stored = cookie_file.read_text()
                self.assertNotIn(EMAIL, stored)
                self.assertNotIn(PASSWORD, stored)
                self.assertNotIn(CODE, stored)
                self.assertEqual(prompt.call_count, 3)
                self.assertEqual(len(fixture.requests), 7)

    def test_reproduces_deployed_preview_failure_at_post_redirect(self):
        archive_path = Path(__file__).parent / 'dist/script.primescene.rewrite-0.0.3.zip'
        if not archive_path.is_file():
            self.skipTest('Removed preview add-on archive is not part of Prime Scene')
        with ZipFile(archive_path) as archive:
            deployed_source = archive.read('script.primescene.rewrite/prime_signin.py')
        module = ModuleType('deployed_prime_signin')
        with patch.dict(sys.modules, {module.__name__: module}):
            exec(compile(deployed_source, 'deployed-0.0.3/prime_signin.py', 'exec'), module.__dict__)
            fixture = SignInFixture(307)
            with patch.object(module, 'build_opener', side_effect=fixture_opener(fixture)):
                with self.assertRaisesRegex(module.SignInError, 'unexpected sign-in redirect'):
                    module.authenticate(LWPCookieJar(), Mock(side_effect=(EMAIL, PASSWORD)))
        self.assertEqual(fixture.requests[-1].full_url, PASSWORD_URL)

    def test_cancel_at_email_code_and_resubmit_an_invalid_code(self):
        for answers, expected, retry_code in (((EMAIL, PASSWORD, None), False, False),
                                               ((EMAIL, PASSWORD, CODE, CODE), True, True)):
            with self.subTest(expected=expected):
                fixture = SignInFixture(307, retry_code=retry_code)
                with patch.object(prime_signin, 'build_opener', side_effect=fixture_opener(fixture)):
                    self.assertIs(prime_signin.authenticate(LWPCookieJar(), Mock(side_effect=answers)), expected)
                self.assertEqual(fixture.code_submissions, 2 if retry_code else 0)

    def test_blocked_redirect_error_contains_no_path_query_or_credentials(self):
        handler = prime_signin.SignInRedirect()
        target = 'https://unrecognized.example.invalid/ap/cvf/private-token?email=' + EMAIL
        with self.assertRaises(prime_signin.SignInError) as error:
            handler.redirect_request(Request(PASSWORD_URL, data=PASSWORD.encode()), None, 302, '', {}, target)
        self.assertIn('unrecognized.example.invalid', str(error.exception))
        for sensitive in (EMAIL, PASSWORD, '/ap/cvf', 'private-token'):
            self.assertNotIn(sensitive, str(error.exception))

    def test_foreign_post_replay_is_rejected_and_safe_get_redirect_drops_secrets(self):
        handler = prime_signin.SignInRedirect()
        for target in ('https://www.primevideo.com/ap/signin', 'https://www.amazon.com/orders',
                       'https://amazon.com/ap/signin', 'http://www.amazon.com/ap/signin',
                       'https://www.amazon.com.evil.invalid/ap/signin'):
            with self.subTest(target=target), self.assertRaises(prime_signin.SignInError):
                handler.redirect_request(Request(PASSWORD_URL, data=PASSWORD.encode()), None, 307, '', {}, target)
        request = Request(PASSWORD_URL, data=PASSWORD.encode(), headers={
            'Cookie': 'private-session=fixture', 'Host': 'www.amazon.com',
            'Authorization': 'private-token', 'Referer': PASSWORD_URL + '?token=fixture',
            'Origin': 'https://www.amazon.com',
        })
        redirected = handler.redirect_request(request, None, 303, '', {}, PRIME_RETURN)
        self.assertEqual(redirected.get_method(), 'GET')
        self.assertIsNone(redirected.data)
        for header in ('Cookie', 'Host', 'Authorization', 'Referer', 'Origin'):
            self.assertIsNone(redirected.get_header(header))


if __name__ == '__main__':
    unittest.main()

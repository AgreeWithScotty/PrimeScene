"""One verified HTTPS policy for the add-on's outbound Python requests."""
from functools import lru_cache
import ssl
from urllib.request import BaseHandler,HTTPSHandler,ProxyHandler,build_opener


@lru_cache(maxsize=1)
def client_context():
    # Construct explicitly: create_default_context honors SSLKEYLOGFILE, which
    # would write traffic decryption keys to disk even for account requests.
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.verify_mode=ssl.CERT_REQUIRED
    context.check_hostname=True
    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    if hasattr(context,'keylog_filename'):context.keylog_filename=None
    return context


class _HTTPSOnly(BaseHandler):
    def default_open(self,request):
        if request.type!='https':raise ValueError('Only verified HTTPS requests are permitted')


def secure_opener(*handlers):
    handlers=list(handlers)
    if not any(isinstance(h,HTTPSHandler) for h in handlers):
        handlers.append(HTTPSHandler(context=client_context()))
    # Do not inherit proxy routing from the environment. Kodi/InputStream's own
    # network implementation remains a separate dependency boundary.
    return build_opener(_HTTPSOnly(),ProxyHandler({}),*handlers)

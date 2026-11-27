"""Account-owned HTTPS keep-alive without changing urllib cookie/redirect policy."""
from http.client import HTTPSConnection,RemoteDisconnected,CannotSendRequest,ResponseNotReady
import hashlib
from secrets import token_hex
import socket
import ssl
from threading import RLock
from time import monotonic
from urllib.error import URLError
from urllib.request import HTTPSHandler
from .prime_diagnostics import exception_details,route


class _Response:
    def __init__(self,response,connection,pool,key,identity,url,observer,reusable):
        self.response=response;self.connection=connection;self.pool=pool;self.key=key
        self.identity=identity;self.url=url;self.observer=observer;self.reusable=reusable
        self.status=self.code=response.status;self.headers=response.headers;self.msg=response.reason
        self.failed=False;self.released=False
    def __getattr__(self,name):return getattr(self.response,name)
    def read(self,*args):
        try:return self.response.read(*args)
        except Exception:self.failed=True;raise
    def readline(self,*args):
        try:return self.response.readline(*args)
        except Exception:self.failed=True;raise
    def readinto(self,*args):
        try:return self.response.readinto(*args)
        except Exception:self.failed=True;raise
    def info(self):return self.headers
    def geturl(self):return self.url
    def getcode(self):return self.code
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def close(self):
        if self.released:return
        self.released=True
        complete=self.response.isclosed() or self.response.length==0
        keep=complete and not self.failed and not self.response.will_close and self.reusable
        try:self.response.close()
        except Exception:
            keep=False;raise
        finally:self.pool.release(self.key,self.connection,self.identity,keep,self.observer)


class CatalogConnections:
    def __init__(self,active,*,operation='catalog'):
        self.active=active;self.lock=RLock();self.idle={};self.closed=False
        self.context=ssl.create_default_context()
        self.operation=operation;self.leased=set()
    def close(self,abort=False):
        with self.lock:
            self.closed=True;idle=self.idle;self.idle={};leased=list(self.leased) if abort else []
        for values in idle.values():
            for connection,_,_ in values:connection.close()
        for connection in leased:
            try:
                # Interrupt a concurrent TLS read at the OS socket, without
                # negotiating SSL shutdown or waiting for its read lock.
                if connection.sock is not None:socket.socket.shutdown(connection.sock,socket.SHUT_RDWR)
            except OSError:pass
            # The response reader owns close/release. Closing its buffered file
            # here can wait on the reader's lock. Its existing network timeout
            # still bounds platforms where shutdown cannot interrupt a TLS read.
    def release(self,key,connection,identity,keep,observer):
        with self.lock:
            self.leased.discard(connection)
            keep=keep and not self.closed and self.active() and connection.sock is not None
            if keep:
                for values in self.idle.values():
                    expired=[entry for entry in values if monotonic()-entry[1]>=20]
                    for entry in expired:values.remove(entry);entry[0].close()
                values=self.idle.setdefault(key,[])
                if len(values)<2 and sum(len(v) for v in self.idle.values())<8:
                    values.append((connection,monotonic(),identity));return
        connection.close()
    def open(self,request,observer):
        if self.closed or not self.active():
            self.close();raise URLError('The request account changed')
        started=monotonic();timeout=request.timeout
        if timeout is socket._GLOBAL_DEFAULT_TIMEOUT:timeout=socket.getdefaulttimeout()
        headers=dict(request.unredirected_hdrs)
        headers.update({k:v for k,v in request.headers.items() if k not in headers})
        headers={name.title():value for name,value in headers.items()}
        headers.setdefault('Connection','keep-alive');headers.setdefault('Accept-Encoding','gzip')
        tunnel=getattr(request,'_tunnel_host',None)
        proxy_auth=headers.pop('Proxy-Authorization',None) if tunnel else None
        # Proxy credentials are never logged or reused across a different tunnel.
        key=(request.host.lower(),tunnel.lower() if tunnel else None,
            hashlib.sha256(proxy_auth.encode()).digest() if proxy_auth else None)
        connection=None;identity=None;reused=False
        with self.lock:
            values=self.idle.get(key,[])
            while values:
                candidate,saved,candidate_id=values.pop()
                if monotonic()-saved<20 and candidate.sock is not None:
                    connection,identity,reused=candidate,candidate_id,True;break
                candidate.close()
        safe_retry=request.get_method() in ('GET','HEAD') and request.data is None
        for retry in range(2):
            if connection is None:
                connection=HTTPSConnection(request.host,timeout=timeout,context=self.context);identity=token_hex(8)
                if tunnel:connection.set_tunnel(tunnel,headers={'Proxy-Authorization':proxy_auth} if proxy_auth else {})
            connection.timeout=timeout
            with self.lock:
                if self.closed:connection.close();raise URLError('The request was cancelled')
                self.leased.add(connection)
            if connection.sock is not None:connection.sock.settimeout(timeout)
            try:
                connection.request(request.get_method(),request.selector,request.data,headers,
                    encode_chunked=request.has_header('Transfer-encoding'))
                response=connection.getresponse()
                observer({'event':self.operation+'_connection','stage':'response_headers',
                    'transport_revision':'account-https-keepalive-v1',
                    'request_id':getattr(request,'_prime_request_id',None),'connection_id':identity,
                    'address':route(request.full_url),'reused':reused,'stale_retry':bool(retry),
                    'proxy_tunnel':bool(tunnel),'status':response.status,
                    'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})
                return _Response(response,connection,self,key,identity,request.full_url,observer,
                    headers['Connection'].lower()!='close')
            except Exception as error:
                connection.close()
                with self.lock:self.leased.discard(connection)
                stale=isinstance(error,(OSError,RemoteDisconnected,CannotSendRequest,ResponseNotReady))
                again=not retry and reused and safe_retry and stale and not self.closed and self.active()
                observer({'event':self.operation+'_connection','stage':'connect_or_headers',
                    'request_id':getattr(request,'_prime_request_id',None),'connection_id':identity,
                    'address':route(request.full_url),'reused':reused,'retrying':again,
                    'elapsed_ms':round((monotonic()-started)*1000),
                    'exception':exception_details(error),'result':'cancelled' if self.closed else 'retry' if again else 'failure'})
                if not again:raise URLError(error) from error
                connection=None;reused=False


class KeepAliveHTTPSHandler(HTTPSHandler):
    def __init__(self,pool,observer):
        super().__init__();self.pool=pool;self.observer=observer or (lambda event:None)
    def https_open(self,request):return self.pool.open(request,self.observer)

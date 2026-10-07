"""Stand-ins for discord / aiohttp / settings so project code can be imported and exercised."""
import sys, types, logging
logging.disable(logging.CRITICAL)
def mod(name, **kw):
    m=types.ModuleType(name); m.__dict__.update(kw); sys.modules[name]=m; return m
mod('utils.logger', logger=logging.getLogger('t'))
class Any_:
    def __init__(s,*a,**k): s.__dict__.update(k); s.children=[]
    def add_item(s,i): s.children.append(i)
    def stop(s): s.stopped=True
    def is_finished(s): return True
    def __getattr__(s,n):
        if n.startswith('__'): raise AttributeError(n)
        return Any_()
    def __call__(s,*a,**k): return Any_()
class Embed:
    def __init__(s,**k): s.title=k.get('title'); s.description=k.get('description'); s.color=k.get('color'); s.fields=[]
    def add_field(s,**k): s.fields.append(k)
    def set_footer(s,**k): pass
class HTTPException(Exception): pass
discord=mod('discord', ui=mod('discord.ui', Select=Any_, Button=Any_, View=Any_), Embed=Embed, Interaction=object, Message=object, ButtonStyle=Any_(), Color=Any_(), SelectOption=Any_, HTTPException=HTTPException, NotFound=HTTPException, utils=Any_(), AllowedMentions=Any_)
class HTTP:
    """Fake aiohttp session; `script` is a callable (method,url,kw)->(status, json)."""
    made=[]
    def __init__(s,**k): s.closed=False; s.logins=0; HTTP.made.append(s)
    def _ctx(s,status,payload):
        class C:
            async def __aenter__(c):
                r=types.SimpleNamespace(status=status, headers={'Content-Type':'application/json'})
                async def text(): return 'x'
                async def js(): return payload
                r.text=text; r.json=js; return r
            async def __aexit__(c,*a): return False
        return C()
    def post(s,url,**k):
        if url.endswith('/auth/login'): s.logins+=1; return s._ctx(200,None)
        return s._ctx(*HTTP.script('POST',url,k))
    def get(s,url,**k): return s._ctx(*HTTP.script('GET',url,k))
    def request(s,m,url,**k): return s._ctx(*HTTP.script(m,url,k))
    async def close(s): s.closed=True
mod('aiohttp', ClientSession=HTTP, ClientTimeout=lambda **k:k, CookieJar=lambda **k:None, ClientResponse=object, ClientError=Exception, BasicAuth=lambda *a:None)
settings=types.SimpleNamespace(QBITTORRENT_HOST='h',QBITTORRENT_PORT=1,QBITTORRENT_TIMEOUT=5,QBITTORRENT_USERNAME='u',QBITTORRENT_PASSWORD='p')
mod('config.settings', settings=settings)

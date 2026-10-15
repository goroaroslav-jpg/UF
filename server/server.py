#!/usr/bin/env python3
"""Authoritative online city prototype and account/admin service for UKRAINE FAMILY.
Run behind HTTPS reverse proxy. Default bind: localhost. No embedded credentials.
"""
import ipaddress
import argparse, base64, getpass, hashlib, hmac, json, os, re, secrets, sqlite3, struct, threading, time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

MAX_BODY = 4096
SESSION_TTL = 900

def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()

def totp(secret, counter=None):
    counter = int(time.time() // 30) if counter is None else counter
    raw = hmac.new(base64.b32decode(secret), struct.pack('>Q', counter), hashlib.sha1).digest()
    off = raw[-1] & 15
    return f'{(struct.unpack(">I", raw[off:off+4])[0] & 0x7fffffff) % 1000000:06d}'

def strict_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON field')
        result[key]=value
    return result

def invalid_constant(value):
    raise ValueError('Non-finite JSON number')

def validate_json_depth(value,depth=0):
    if depth>16:raise ValueError('JSON nesting limit')
    if isinstance(value,dict):
        for k,v in value.items():validate_json_depth(v,depth+1)
    elif isinstance(value,list):
        for v in value:validate_json_depth(v,depth+1)

from server_errors import Failure
from world import CityWorld

class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt BLOB NOT NULL,
          password TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('player','moderator','owner')),
          otp TEXT NOT NULL DEFAULT '', otp_step INTEGER NOT NULL DEFAULT -1,
          banned INTEGER NOT NULL DEFAULT 0, frozen INTEGER NOT NULL DEFAULT 0,
          mute_until INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, user_id INTEGER NOT NULL
          REFERENCES users(id), expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at INTEGER NOT NULL,
          actor INTEGER NOT NULL, target INTEGER NOT NULL, action TEXT NOT NULL,
          reason TEXT NOT NULL, prev TEXT NOT NULL, digest TEXT NOT NULL);
        ''')
        self.db.executescript('CREATE INDEX IF NOT EXISTS sessions_expires ON sessions(expires); CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);')
        self.db.executescript('CREATE TABLE IF NOT EXISTS chat (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,username TEXT NOT NULL,text TEXT NOT NULL,at INTEGER NOT NULL,request_id TEXT NOT NULL,UNIQUE(user_id,request_id));')
        self.game=CityWorld(self.db)
        self.next_cleanup = 0.0
        self.dummy_salt = secrets.token_bytes(16)
        self.dummy_hash = password_hash(secrets.token_urlsafe(32), self.dummy_salt)
        self.limits = OrderedDict()

    def create(self, username, password, role):
        if not re.fullmatch(r'[A-Za-z0-9_]{3,24}', username):
            raise ValueError('Username must be 3-24 ASCII letters, digits or underscore')
        if not 14 <= len(password) <= 128:
            raise ValueError('Password must have 14-128 characters')
        if role not in ('player','moderator','owner'):
            raise ValueError('Invalid role')
        secret = base64.b32encode(secrets.token_bytes(20)).decode() if role != 'player' else ''
        salt = secrets.token_bytes(16)
        hashed = password_hash(password, salt)
        with self.lock:
            self.db.execute('INSERT INTO users(username,salt,password,role,otp) VALUES(?,?,?,?,?)',
                            (username,salt,hashed,role,secret))
        return secret

    def rate(self, key, limit, window):
        now = time.monotonic()
        times = [t for t in self.limits.pop(key, []) if now-t < window]
        if len(times) >= limit:
            self.limits[key] = times
            raise Failure(429, 'Забагато запитів. Спробуйте пізніше.')
        times.append(now)
        self.limits[key] = times
        while len(self.limits) > 10000:
            self.limits.popitem(last=False)

    def audit(self, actor, target, action, reason):
        prev = self.db.execute('SELECT digest FROM audit ORDER BY id DESC LIMIT 1').fetchone()
        prev = prev[0] if prev else '0'*64
        at = int(time.time())
        raw = json.dumps([at,actor,target,action,reason,prev],ensure_ascii=False,separators=(',',':'))
        digest = hashlib.sha256(raw.encode()).hexdigest()
        self.db.execute('INSERT INTO audit(at,actor,target,action,reason,prev,digest) VALUES(?,?,?,?,?,?,?)',
                        (at,actor,target,action,reason,prev,digest))

    def identity(self, token):
        if not isinstance(token,str) or not 40 <= len(token) <= 128:
            raise Failure(401,'Потрібен вхід.')
        digest=hashlib.sha256(token.encode()).hexdigest()
        user=self.db.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id '
                             'WHERE s.digest=? AND s.expires>? AND u.banned=0',
                             (digest,time.time())).fetchone()
        if not user:
            raise Failure(401,'Сесія недійсна або завершена.')
        return user

    def dispatch(self, path, data, token='', ip='local'):
        if not isinstance(data,dict):
            raise Failure(400,'Очікується JSON-об’єкт.')
        with self.lock:
            self.rate(('ip-world' if path=='/world/tick' else 'ip',ip),9000 if path=='/world/tick' else 90,60)
            now=time.monotonic()
            if now>=self.next_cleanup:
                self.db.execute('DELETE FROM sessions WHERE expires<=?',(time.time(),))
                self.next_cleanup=now+60
            if path == '/register':
                self.rate(('register',ip),3,300)
                name,password=data.get('username'),data.get('password')
                if not isinstance(name,str) or not isinstance(password,str):raise Failure(400,'Потрібні ім’я та пароль.')
                try:self.create(name,password,'player')
                except (ValueError,UnicodeError):raise Failure(400,'Логін: 3–24 латинські символи/цифри/_. Пароль: 14–128 символів.')
                except sqlite3.IntegrityError:raise Failure(409,'Цей логін недоступний.')
                return {'ok':True}
            if path == '/login':
                name=data.get('username','')
                password=data.get('password','')
                code=data.get('otp','')
                if not all(isinstance(v,str) for v in (name,password,code)) or not re.fullmatch(r'[A-Za-z0-9_]{3,24}',name) or len(password)>128 or (code and not re.fullmatch(r'[0-9]{6}',code)):
                    raise Failure(400,'Некоректний формат входу.')
                try:password.encode('utf-8')
                except UnicodeError:raise Failure(400,'Некоректний формат входу.')
                # Both account and source limits; never trust X-Forwarded-For from a client.
                self.rate(('login-ip',ip),5,300)
                self.rate(('login-name',name.lower()),5,300)
                u=self.db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
                expected=u['password'] if u else self.dummy_hash
                salt=u['salt'] if u else self.dummy_salt
                good=hmac.compare_digest(password_hash(password,salt),expected)
                if not u or not good or u['banned']:
                    raise Failure(401,'Невірні дані входу.')
                accepted=None
                if u['role'] != 'player':
                    step=int(time.time()//30)
                    accepted=next((k for k in (step-1,step,step+1)
                                   if k>u['otp_step'] and hmac.compare_digest(totp(u['otp'],k),code)),None)
                    if accepted is None:
                        raise Failure(401,'Невірні дані входу.')
                token=secrets.token_urlsafe(32)
                self.db.execute('BEGIN IMMEDIATE')
                try:
                    if accepted is not None:self.db.execute('UPDATE users SET otp_step=? WHERE id=?',(accepted,u['id']))
                    self.db.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],))
                    self.db.execute('INSERT INTO sessions VALUES(?,?,?)',
                                    (hashlib.sha256(token.encode()).hexdigest(),u['id'],time.time()+SESSION_TTL))
                    self.audit(u['id'],u['id'],'login','session created')
                    self.db.execute('COMMIT')
                except Exception:
                    self.db.execute('ROLLBACK');raise
                return {'token':token,'expires_in':SESSION_TTL,'role':u['role']}
            u=self.identity(token)
            self.rate(('user-world' if path=='/world/tick' else 'user',u['id']),600 if path=='/world/tick' else 45,60)
            if path in ('/chat/list','/chat/send'):
                if path=='/chat/send':
                    request=data.get('request_id');message=data.get('text')
                    if not isinstance(request,str) or not re.fullmatch('[a-f0-9]{32}',request):raise Failure(400,'Потрібен ID повідомлення.')
                    if not isinstance(message,str) or not 1<=len(message.strip())<=160 or any(ord(c)<32 or 0xD800<=ord(c)<=0xDFFF for c in message):raise Failure(400,'Повідомлення: 1–160 символів без переносів.')
                    message=message.strip()
                    if u['mute_until']>time.time():raise Failure(403,'Чат тимчасово заборонено адміністратором.')
                    old=self.db.execute('SELECT text FROM chat WHERE user_id=? AND request_id=?',(u['id'],request)).fetchone()
                    if old:
                        if old[0]!=message:raise Failure(409,'ID повідомлення вже використано.')
                    else:
                        self.rate(('chat-send',u['id']),1,2)
                        self.db.execute('INSERT INTO chat(user_id,username,text,at,request_id) VALUES(?,?,?,?,?)',(u['id'],u['username'],message,int(time.time()),request))
                        self.db.execute('DELETE FROM chat WHERE id <= (SELECT COALESCE(MAX(id),0)-500 FROM chat)')
                rows=self.db.execute('SELECT id,username,text,at FROM chat ORDER BY id DESC LIMIT 40').fetchall()
                return {'messages':[dict(v) for v in reversed(rows)]}
            if path=='/world/join':return self.game.join(u)
            if path=='/world/tick':return self.game.tick(u,data)
            if path=='/world/action':return self.game.action(u,data)
            if path == '/logout':
                self.game.leave(u['id'])
                self.db.execute('DELETE FROM sessions WHERE digest=?',(hashlib.sha256(token.encode()).hexdigest(),))
                return {'ok':True}
            if path == '/me':
                return {k:u[k] for k in ('id','username','role','frozen','mute_until')}
            if not path.startswith('/admin/'):
                raise Failure(404,'Невідомий маршрут.')
            if u['role'] not in ('moderator','owner'):
                raise Failure(403,'Недостатньо прав.')
            if path == '/admin/users':
                return {'users':[dict(v) for v in self.db.execute(
                    'SELECT id,username,role,banned,frozen,mute_until FROM users ORDER BY id LIMIT 500')]}
            if path == '/admin/audit':
                if u['role'] != 'owner':
                    raise Failure(403,'Доступ лише власнику.')
                return {'audit':[dict(v) for v in self.db.execute(
                    'SELECT id,at,actor,target,action,reason,digest FROM audit ORDER BY id DESC LIMIT 100')]}
            if path != '/admin/action':
                raise Failure(404,'Невідомий маршрут.')
            action,reason,target = data.get('action'),data.get('reason'),data.get('target')
            if not isinstance(action,str) or action not in ('freeze','unfreeze','ban','unban','mute','unmute'):
                raise Failure(400,'Невідома дія.')
            if not isinstance(reason,str) or not 3<=len(reason.strip())<=200 or any(ord(c)<32 or 0xd800<=ord(c)<=0xdfff for c in reason):
                raise Failure(400,'Вкажіть причину (3–200 символів).')
            if isinstance(target,bool) or not isinstance(target,(str,int)) or not re.fullmatch('[0-9]{1,9}',str(target)):
                raise Failure(400,'Некоректний ID.')
            target=int(target)
            t=self.db.execute('SELECT * FROM users WHERE id=?',(target,)).fetchone()
            if not t:
                raise Failure(404,'Акаунт не знайдено.')
            if t['id']==u['id'] or t['role']=='owner' or (u['role']=='moderator' and t['role']!='player'):
                raise Failure(403,'Не можна змінити цей акаунт.')
            if action in ('ban','unban') and u['role']!='owner':
                raise Failure(403,'Блокування доступне власнику.')
            column,value={'freeze':('frozen',1),'unfreeze':('frozen',0),
                          'ban':('banned',1),'unban':('banned',0),
                          'mute':('mute_until',int(time.time())+3600),'unmute':('mute_until',0)}[action]
            self.db.execute('BEGIN IMMEDIATE')
            try:
                # column is an internal allowlisted constant, values remain parameterized.
                self.db.execute(f'UPDATE users SET {column}=? WHERE id=?',(value,target))
                if action=='ban':self.db.execute('DELETE FROM sessions WHERE user_id=?',(target,))
                self.audit(u['id'],target,action,reason.strip())
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK');raise
            if action=='ban':self.game.active.pop(target,None)
            return {'ok':True}

def client_ip(peer, headers, trusted_proxies=()):
    """Only explicit reverse-proxy peers may supply the overwritten client-IP header."""
    try:
        address=ipaddress.ip_address(peer)
        if any(address in network for network in trusted_proxies):
            values=headers.get_all('X-UF-Client-IP',[])
            if len(values)==1:return str(ipaddress.ip_address(values[0].strip()))
    except ValueError:pass
    return peer

class Handler(BaseHTTPRequestHandler):
    server_version='UKRAINE-FAMILY'
    sys_version=''
    def setup(self):
        super().setup();self.connection.settimeout(5)
    def log_message(self,*args):pass  # Never write credentials or tokens to access logs.
    def do_GET(self):
        status=200 if self.path=='/health' else 404
        payload=json.dumps({'ok':True,'version':'8.0','build':'8.2-unity','protocol':8,'features':['interiors','cart','appearance']} if status==200 else {'error':'Not found'}).encode()
        self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.send_header('Cache-Control','no-store');self.send_header('Connection','close');self.end_headers()
        try:self.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError):pass
        self.close_connection=True
    def do_POST(self):
        try:
            if self.headers.get('Transfer-Encoding'):
                raise Failure(400,'Transfer-Encoding not supported')
            if len(self.headers.get_all('Content-Length',[]))!=1 or len(self.headers.get_all('Authorization',[]))>1:
                raise Failure(400,'Неоднозначні заголовки.')
            length=self.headers.get('Content-Length','')
            if not length.isdigit() or not 0<int(length)<=MAX_BODY:
                raise Failure(413,'Запит завеликий або порожній.')
            if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
                raise Failure(415,'Потрібен application/json.')
            raw=self.rfile.read(int(length))
            if len(raw)!=int(length):raise Failure(400,'Неповний запит.')
            data=json.loads(raw,object_pairs_hook=strict_pairs,parse_constant=invalid_constant)
            validate_json_depth(data)
            auth=self.headers.get('Authorization','')
            token=auth[7:] if auth.startswith('Bearer ') else ''
            out=self.server.store.dispatch(self.path,data,token,client_ip(self.client_address[0],self.headers,self.server.trusted_proxies));status=200
        except Failure as e:out={'error':e.message};status=e.status
        except (ValueError,UnicodeError,RecursionError):out={'error':'Некоректний JSON.'};status=400
        except Exception:out={'error':'Внутрішня помилка.'};status=500
        payload=json.dumps(out,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(payload)));self.send_header('Connection','close');self.end_headers()
        try:self.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError):pass
        self.close_connection=True

class BoundedServer(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=32
    def __init__(self,*a,**kw):
        self.trusted_proxies=tuple(ipaddress.ip_network(v.strip(),strict=False) for v in os.environ.get("UF_TRUSTED_PROXIES","").split(",") if v.strip());self.slots=threading.BoundedSemaphore(16);super().__init__(*a,**kw)
    def process_request(self,request,address):
        if not self.slots.acquire(False):self.shutdown_request(request);return
        try:super().process_request(request,address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='family.sqlite3')
    sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('create-user');create.add_argument('username');create.add_argument('--role',choices=['owner','moderator','player'],default='player')
    serve=sub.add_parser('serve');serve.add_argument('--port',type=int,default=8765);serve.add_argument('--bind',default='127.0.0.1')
    sub.add_parser('revoke-all')
    args=parser.parse_args();store=Store(args.db)
    if args.command=='create-user':
        password=getpass.getpass('Password (14+ characters): ')
        if password!=getpass.getpass('Repeat password: '):raise SystemExit('Passwords differ')
        secret=store.create(args.username,password,args.role)
        if secret:
            print('PRIVATE TOTP secret (add to your authenticator, keep offline):',secret)
            print('otpauth://totp/UKRAINE%20FAMILY:'+quote(args.username)+'?secret='+secret+'&issuer=UKRAINE%20FAMILY')
        print('Account created')
    elif args.command=='revoke-all':
        store.db.execute('DELETE FROM sessions');print('All sessions revoked')
    else:
        server=BoundedServer((args.bind,args.port),Handler);server.store=store
        print('Admin API on localhost; expose ONLY behind HTTPS reverse proxy.')
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()
if __name__=='__main__':main()

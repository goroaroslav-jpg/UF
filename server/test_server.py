import unittest, tempfile, os, time, hashlib, json, threading, http.client
from unittest.mock import patch
from server import Store, Failure, totp, BoundedServer, Handler

class SecurityTests(unittest.TestCase):
 def setUp(self):
  self.store=Store(':memory:');self.secret=self.store.create('UKR_OGONEK','Owner-password-!2026','owner')
  self.modsecret=self.store.create('Moderator','Moderator-pass-!2026','moderator')
  self.store.create('Test_Player','Player-password-!2026','player')
  self.owner=self.login('UKR_OGONEK','Owner-password-!2026',self.secret)
  self.mod=self.login('Moderator','Moderator-pass-!2026',self.modsecret)
  self.player=self.login('Test_Player','Player-password-!2026','')
 def tearDown(self):self.store.db.close()
 def login(self,name,pw,secret):return self.store.dispatch('/login',{'username':name,'password':pw,'otp':totp(secret) if secret else ''},ip=name)['token']
 def fails(self,code,path,data=None,token=''):
  with self.assertRaises(Failure) as cm:self.store.dispatch(path,data or {},token)
  self.assertEqual(cm.exception.status,code)
 def action(self,action,token=None,target='3',reason='Test moderation'):
  return self.store.dispatch('/admin/action',dict(action=action,target=target,reason=reason),token or self.owner)
 def test_unauthenticated_denied(self):self.fails(401,'/admin/users')
 def test_player_cannot_admin(self):self.fails(403,'/admin/users',token=self.player)
 def test_no_role_from_client(self):self.fails(403,'/admin/action',{'role':'owner','target':'3','action':'ban','reason':'abc'},self.player)
 def test_mod_cannot_ban(self):
  with self.assertRaises(Failure) as e:self.action('ban',self.mod)
  self.assertEqual(e.exception.status,403)
 def test_protect_owner(self):
  with self.assertRaises(Failure):self.action('ban',target='1')
 def test_ban_revokes_session(self):
  self.action('ban');self.fails(401,'/me',token=self.player)
  self.fails(401,'/login',{'username':'Test_Player','password':'Player-password-!2026','otp':''})
 def test_unban(self):
  self.action('ban');self.action('unban');self.assertTrue(self.login('Test_Player','Player-password-!2026',''))
 def test_freeze_and_mute(self):
  self.action('freeze',self.mod);self.action('mute',self.mod)
  u=self.store.dispatch('/me',{},self.player);self.assertEqual(u['frozen'],1);self.assertGreater(u['mute_until'],time.time())
  self.action('unfreeze');self.action('unmute');u=self.store.dispatch('/me',{},self.player);self.assertEqual(u['frozen'],0);self.assertEqual(u['mute_until'],0)
 def test_invalid_action(self):
  with self.assertRaises(Failure):self.action('grant_owner')
 def test_reason_required(self):
  with self.assertRaises(Failure):self.action('ban',reason='')
 def test_sql_injection_id(self):
  with self.assertRaises(Failure):self.action('ban',target='3 OR 1=1')
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM users WHERE banned=1').fetchone()[0],0)
 def test_2fa_replay(self):self.fails(401,'/login',{'username':'UKR_OGONEK','password':'Owner-password-!2026','otp':totp(self.secret)})
 def test_missing_2fa(self):self.fails(401,'/login',{'username':'UKR_OGONEK','password':'Owner-password-!2026','otp':''})
 def test_expired_session(self):
  self.store.db.execute('UPDATE sessions SET expires=0');self.fails(401,'/admin/users',token=self.owner)
 def test_logout(self):
  self.store.dispatch('/logout',{},self.owner);self.fails(401,'/admin/users',token=self.owner)
 def test_tokens_not_stored_plain(self):
  digests=[r[0] for r in self.store.db.execute('SELECT digest FROM sessions')];self.assertNotIn(self.owner,digests);self.assertIn(hashlib.sha256(self.owner.encode()).hexdigest(),digests)
 def test_rate_limit(self):
  for i in range(5):self.fails(401,'/login',{'username':'Nobody','password':'wrong','otp':''})
  self.fails(429,'/login',{'username':'Nobody','password':'wrong','otp':''})
 def test_audit_chain(self):
  self.action('freeze');prev='0'*64
  for r in self.store.db.execute('SELECT * FROM audit ORDER BY id'):
   self.assertEqual(r['prev'],prev);raw=json.dumps([r['at'],r['actor'],r['target'],r['action'],r['reason'],r['prev']],ensure_ascii=False,separators=(',',':'));self.assertEqual(r['digest'],hashlib.sha256(raw.encode()).hexdigest());prev=r['digest']
 def test_no_secrets_in_lists(self):
  out=self.store.dispatch('/admin/users',{},self.owner)
  for u in out['users']:self.assertFalse(set(u)&{'password','salt','otp','token'})
 def test_http_boundary(self):
  server=BoundedServer(('127.0.0.1',0),Handler);server.store=self.store;t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  try:
   c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('POST','/login',body=b'[]',headers={'Content-Type':'application/json'});r=c.getresponse();self.assertEqual(r.status,400);r.read();c.close()
   c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('POST','/login',body=b'x'*4097,headers={'Content-Type':'application/json'});r=c.getresponse();self.assertEqual(r.status,413);r.read();c.close()
  finally:server.shutdown();server.server_close();t.join()

 def test_unicode_otp_rejected_without_internal_error(self):
  self.fails(400,'/login',{'username':'UKR_OGONEK','password':'Owner-password-!2026','otp':'абвгде'})
 def test_login_transaction_rolls_back_on_audit_failure(self):
  secret=self.store.create('Backup_Admin','Backup-password-!2026','owner')
  with patch.object(self.store,'audit',side_effect=RuntimeError('disk failed')):
   with self.assertRaises(RuntimeError):self.login('Backup_Admin','Backup-password-!2026',secret)
  row=self.store.db.execute("SELECT id,otp_step FROM users WHERE username='Backup_Admin'").fetchone()
  self.assertEqual(row['otp_step'],-1)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM sessions WHERE user_id=?',(row['id'],)).fetchone()[0],0)
  self.assertTrue(self.login('Backup_Admin','Backup-password-!2026',secret))
 def test_ban_transaction_rolls_back_on_audit_failure(self):
  with patch.object(self.store,'audit',side_effect=RuntimeError('disk failed')):
   with self.assertRaises(RuntimeError):self.action('ban')
  self.assertEqual(self.store.identity(self.player)['banned'],0)
 def test_mod_cannot_modify_peer(self):
  self.fails(403,'/admin/action',{'target':'2','action':'freeze','reason':'abc'},self.mod)
 def test_surrogate_reason_rejected(self):
  self.fails(400,'/admin/action',{'target':'3','action':'ban','reason':'abc'+chr(0xd800)},self.owner)
 def test_strict_http_json(self):
  server=BoundedServer(('127.0.0.1',0),Handler);server.store=self.store;t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  try:
   for body in [b'{"username":"abc","username":"xyz"}',b'{"x":NaN}',b'{"x":'+b'['*1000+b'0'+b']'*1000+b'}']:
    c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('POST','/login',body=body,headers={'Content-Type':'application/json'});r=c.getresponse();self.assertEqual(r.status,400);r.read();c.close()
  finally:server.shutdown();server.server_close();t.join()
 def test_ambiguous_length_rejected(self):
  server=BoundedServer(('127.0.0.1',0),Handler);server.store=self.store;t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  try:
   c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.putrequest('POST','/login');c.putheader('Content-Type','application/json');c.putheader('Content-Length','2');c.putheader('Content-Length','3');c.endheaders(b'{}');r=c.getresponse();self.assertEqual(r.status,400);r.read();c.close()
  finally:server.shutdown();server.server_close();t.join()

 def test_expired_session_denied_between_cleanups(self):
  self.store.next_cleanup=time.monotonic()+60
  self.store.db.execute('UPDATE sessions SET expires=0')
  self.fails(401,'/admin/users',token=self.owner)
 def test_session_cleanup_after_deadline(self):
  self.store.db.execute('UPDATE sessions SET expires=0');self.store.next_cleanup=0
  self.fails(401,'/me',token=self.player)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM sessions').fetchone()[0],0)

if __name__=='__main__':unittest.main()

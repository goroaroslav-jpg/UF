import unittest,uuid,json,threading,http.client,sqlite3,tempfile
from pathlib import Path
from server import Store,Failure,BoundedServer,Handler
from backup import backup
class ChatTests(unittest.TestCase):
 def setUp(self):
  self.s=Store(':memory:');self.s.create('Player_One','Player-One-password-2026','player');self.s.create('Player_Two','Player-Two-password-2026','player');self.a=self.s.dispatch('/login',dict(username='Player_One',password='Player-One-password-2026'))['token'];self.b=self.s.dispatch('/login',dict(username='Player_Two',password='Player-Two-password-2026'))['token']
 def tearDown(self):self.s.db.close()
 def send(self,text,**kw):return self.s.dispatch('/chat/send',dict(text=text,request_id=kw.pop('request_id',uuid.uuid4().hex),**kw),self.a)
 def test_shared_sender_identity_and_deduplication(self):
  rid=uuid.uuid4().hex;self.send('Привіт, Україно!',username='OWNER',request_id=rid);self.send('Привіт, Україно!',request_id=rid);rows=self.s.dispatch('/chat/list',{},self.b)['messages'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['username'],'Player_One');self.assertEqual(rows[0]['text'],'Привіт, Україно!')
 def test_id_cannot_change_text(self):
  rid=uuid.uuid4().hex;self.send('One',request_id=rid)
  with self.assertRaises(Failure):self.send('Two',request_id=rid)
 def test_spam_rejected(self):
  self.send('One')
  with self.assertRaises(Failure) as e:self.send('Two')
  self.assertEqual(e.exception.status,429)
 def test_mute_really_blocks_chat(self):
  self.s.db.execute('UPDATE users SET mute_until=9999999999 WHERE id=1')
  with self.assertRaises(Failure) as e:self.send('Muted')
  self.assertEqual(e.exception.status,403)
 def test_invalid_text_and_missing_auth(self):
  for v in ['', 'x'*161, 'a\nb','a\x00b',[],123]:
   with self.assertRaises(Failure):self.send(v)
  with self.assertRaises(Failure):self.s.dispatch('/chat/list',{})
 def test_chat_bounded_history(self):
  for i in range(60):self.s.db.execute('INSERT INTO chat(user_id,username,text,at,request_id) VALUES(1,?,?,?,?)',('Player_One',str(i),i,uuid.uuid4().hex))
  rows=self.s.dispatch('/chat/list',{},self.b)['messages'];self.assertEqual(len(rows),40);self.assertEqual(rows[0]['text'],'20');self.assertEqual(rows[-1]['text'],'59')
 def test_http_chat_and_health(self):
  server=BoundedServer(('127.0.0.1',0),Handler);server.store=self.s;t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  try:
   c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('GET','/health');r=c.getresponse();self.assertEqual(r.status,200);self.assertEqual(json.loads(r.read())['version'],'8.0');c.close()
   c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('POST','/chat/send',json.dumps(dict(text='Онлайн',request_id=uuid.uuid4().hex)),{'Content-Type':'application/json','Authorization':'Bearer '+self.a});r=c.getresponse();self.assertEqual(r.status,200);self.assertEqual(json.loads(r.read())['messages'][-1]['text'],'Онлайн');c.close()
  finally:server.shutdown();server.server_close();t.join()
class BackupTests(unittest.TestCase):
 def test_live_wal_backup_and_no_overwrite(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'source.sqlite3';q=Path(d)/'backup.sqlite3';c=sqlite3.connect(p);c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE sample(value)');c.execute('INSERT INTO sample VALUES(72)');c.commit();backup(p,q)
   with sqlite3.connect(q) as b:self.assertEqual(b.execute('SELECT value FROM sample').fetchone()[0],72)
   with self.assertRaises(ValueError):backup(p,q)
   c.close()

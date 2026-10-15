import unittest,json,uuid,threading,http.client
from unittest.mock import patch
from server import Store,Failure,BoundedServer,Handler
class WorldTests(unittest.TestCase):
 def setUp(self):
  self.store=Store(':memory:');self.store.create('Player_One','Player-One-password-2026','player');self.store.create('Player_Two','Player-Two-password-2026','player');self.a=self.store.db.execute('SELECT * FROM users WHERE id=1').fetchone();self.b=self.store.db.execute('SELECT * FROM users WHERE id=2').fetchone();self.now=100.;self.game=self.store.game;self.game.clock=lambda:self.now;self.game.join(self.a);self.game.join(self.b)
 def tearDown(self):self.store.db.close()
 def state(self):return self.game.get(self.a)['data']
 def act(self,cmd,**kw):return self.game.action(self.a,dict(cmd=cmd,request_id=kw.pop('request_id',uuid.uuid4().hex),**kw))
 def test_two_players_presence(self):
  r=self.game.snapshot(self.a);self.assertEqual(r['online'],2);self.assertEqual(r['players'][0]['id'],2);self.assertEqual(r['players'][0]['username'],'Player_Two')
 def test_server_time_and_input_only(self):
  self.now+=.2;r=self.game.tick(self.a,dict(seq=1,mx=0,my=-1,yaw=0,dt=999,money=999999,x=90,z=90));self.assertAlmostEqual(r['state']['z'],5.3);self.assertEqual(r['state']['money'],0);self.assertEqual(r['state']['x'],0)
 def test_no_movement_without_elapsed_time(self):
  self.game.tick(self.a,dict(seq=1,mx=1,my=0,yaw=0));self.assertEqual(self.state()['x'],0)
 def test_replay_sequence_rejected(self):
  self.game.tick(self.a,dict(seq=1));
  with self.assertRaises(Failure):self.game.tick(self.a,dict(seq=1))
 def test_nan_input_rejected(self):
  with self.assertRaises(Failure):self.game.tick(self.a,dict(seq=1,mx=float('nan')))
 def test_buy_persists_and_is_idempotent(self):
  self.state().update(x=8,z=-20,money=100);request=uuid.uuid4().hex
  r=self.act('buy',place=0,item=0,request_id=request);self.assertEqual(r['state']['money'],85);self.assertEqual(r['state']['bag'][0],1)
  r=self.act('buy',place=0,item=0,request_id=request);self.assertEqual(r['state']['money'],85);self.assertEqual(r['state']['bag'][0],1)
  row=self.store.db.execute('SELECT state FROM game_state WHERE user_id=1').fetchone();self.assertEqual(json.loads(row[0])['money'],85)
 def test_cannot_reuse_operation_for_different_item(self):
  self.state().update(x=8,z=-20,money=100);request=uuid.uuid4().hex;self.act('buy',place=0,item=0,request_id=request)
  with self.assertRaises(Failure):self.act('buy',place=0,item=1,request_id=request)
 def test_remote_purchase_rejected(self):
  self.state()['money']=100
  with self.assertRaises(Failure):self.act('buy',place=0,item=0)
  self.assertEqual(self.state()['money'],100)
 def test_insufficient_funds(self):
  self.state().update(x=8,z=-20,money=10)
  with self.assertRaises(Failure):self.act('buy',place=0,item=0)
  self.assertEqual(self.state()['bag'][0],0);self.assertEqual(self.state()['money'],10)
 def test_inventory_use(self):
  self.state().update(energy=20,bag=[1,0,0,0,0]);self.act('use',item=0);self.assertEqual(self.state()['energy'],35);self.assertEqual(self.state()['bag'][0],0)
  with self.assertRaises(Failure):self.act('use',item=0)
 def test_reward_once(self):
  self.act('job');self.state().update(x=0,z=-42);self.now+=.2;self.game.tick(self.a,dict(seq=1));self.now+=.2;self.game.tick(self.a,dict(seq=2));self.assertEqual(self.state()['money'],250);self.assertEqual(self.state()['deliveries'],1)
 def test_frozen_movement(self):
  self.store.db.execute('UPDATE users SET frozen=1 WHERE id=1');u=self.store.db.execute('SELECT * FROM users WHERE id=1').fetchone();self.now+=.2;self.game.tick(u,dict(seq=1,mx=1,my=0));self.assertEqual(self.state()['x'],0)
  with self.assertRaises(Failure):self.game.action(u,dict(cmd='job',request_id=uuid.uuid4().hex))
 def test_offline_presence_expires(self):
  self.now+=11;self.game.tick(self.a,dict(seq=1));r=self.game.snapshot(self.a);self.assertEqual(r['online'],1);self.assertEqual(r['players'],[])
 def test_action_failure_rolls_back_memory(self):
  self.state().update(x=8,z=-20,money=100)
  with patch.object(self.game,'save',side_effect=RuntimeError('disk failure')):
   with self.assertRaises(RuntimeError):self.act('buy',place=0,item=0)
  self.assertEqual(self.state()['money'],100);self.assertEqual(self.state()['bag'][0],0)
 def test_real_http_two_sessions(self):
  server=BoundedServer(('127.0.0.1',0),Handler);server.store=self.store;t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  def call(path,data,token=''):
   c=http.client.HTTPConnection('127.0.0.1',server.server_port);headers={'Content-Type':'application/json'}
   if token:headers['Authorization']='Bearer '+token
   c.request('POST',path,json.dumps(data),headers);r=c.getresponse();out=json.loads(r.read());status=r.status;c.close();self.assertEqual(status,200,out);return out
  try:
   a=call('/login',dict(username='Player_One',password='Player-One-password-2026'))['token'];b=call('/login',dict(username='Player_Two',password='Player-Two-password-2026'))['token'];call('/world/join',{},a);call('/world/join',{},b);self.now+=.2;call('/world/tick',dict(seq=1,mx=1,my=0,yaw=0),b);r=call('/world/tick',dict(seq=1),a);self.assertEqual(r['online'],2);self.assertGreater(r['players'][0]['x'],0)
  finally:server.shutdown();server.server_close();t.join()
 def test_register_cannot_choose_owner_role(self):
  self.store.dispatch('/register',dict(username='New_Player',password='New-player-password-2026',role='owner'),ip='register-test')
  self.assertEqual(self.store.db.execute("SELECT role FROM users WHERE username='New_Player'").fetchone()[0],'player')
 def test_prune_saves_inactive_state(self):
  self.state()['money']=123;self.now+=61;self.game.prune();self.assertNotIn(1,self.game.active);self.assertEqual(json.loads(self.store.db.execute('SELECT state FROM game_state WHERE user_id=1').fetchone()[0])['money'],123)
 def test_expired_token_cannot_tick(self):
  token=self.store.dispatch('/login',dict(username='Player_One',password='Player-One-password-2026'),ip='expiry')['token'];self.store.db.execute('UPDATE sessions SET expires=0')
  with self.assertRaises(Failure) as cm:self.store.dispatch('/world/tick',dict(seq=1),'%s'%token)
  self.assertEqual(cm.exception.status,401)

if __name__=='__main__':unittest.main()

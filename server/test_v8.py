import unittest,json,uuid
from server import Store,Failure
class VersionEightTests(unittest.TestCase):
 def setUp(self):
  self.store=Store(':memory:');self.store.create('Driver_One','Driver-password-2026','player');self.user=self.store.db.execute('SELECT * FROM users WHERE id=1').fetchone();self.now=10.;self.game=self.store.game;self.game.clock=lambda:self.now;self.game.join(self.user)
 def tearDown(self):self.store.db.close()
 def state(self):return self.game.get(self.user)['data']
 def action(self,cmd,rid=None):return self.game.action(self.user,dict(cmd=cmd,request_id=rid or uuid.uuid4().hex))
 def test_brake_stops_forward_and_reverse_without_acceleration(self):
  for initial in (8.,-6.):
   self.state().update(driving=True,car_x=0,car_z=0,speed=initial)
   for seq in range(1,6):self.now+=.2;self.game.get(self.user)['seq']=-1;self.game.tick(self.user,dict(seq=seq,my=-1,brake=True))
   self.assertEqual(self.state()['speed'],0)
 def test_brake_requires_boolean_without_consuming_sequence(self):
  for bad in (1,'true',[],None):
   with self.assertRaises(Failure):self.game.tick(self.user,dict(seq=1,brake=bad))
  self.now+=.2;self.game.tick(self.user,dict(seq=1,brake=False))
 def test_moving_exit_rejected_and_state_unchanged(self):
  self.state().update(driving=True,speed=8.,car_x=0,car_z=0)
  with self.assertRaises(Failure) as cm:self.action('car')
  self.assertEqual(cm.exception.status,409);self.assertTrue(self.state()['driving']);self.assertEqual(self.state()['speed'],8)
 def test_exit_tries_other_side(self):
  self.state().update(driving=True,speed=0.,car_x=8.1,car_z=-20.,angle=0.)
  self.action('car');self.assertFalse(self.state()['driving']);self.assertLess(self.state()['x'],8.1)
 def test_both_sides_blocked_reject_without_teleport(self):
  self.state().update(driving=True,speed=0.,car_x=0,car_z=0)
  original=self.game.clear;self.game.clear=lambda x,z,r:False
  try:
   with self.assertRaises(Failure):self.action('car')
   self.assertTrue(self.state()['driving'])
  finally:self.game.clear=original
 def test_cancel_delivery_idempotent_without_reward(self):
  self.action('job');rid=uuid.uuid4().hex;self.action('cancel_job',rid);self.action('cancel_job',rid);self.assertEqual(self.state()['mission'],-1);self.assertEqual(self.state()['money'],0);self.assertEqual(self.state()['deliveries'],0)
  with self.assertRaises(Failure):self.action('cancel_job')
 def test_cancel_then_start_new_delivery(self):
  self.action('job');self.action('cancel_job');self.action('job');self.assertEqual(self.state()['mission'],0)
 def test_delivery_requires_stop_when_driving(self):
  self.action('job');self.state().update(driving=True,car_x=0.,car_z=-42.,speed=12.)
  self.game.tick(self.user,dict(seq=1));self.assertEqual(self.state()['money'],0)
  self.state()['speed']=0.;self.game.tick(self.user,dict(seq=2));self.assertEqual(self.state()['money'],250)
 def test_corrupt_save_does_not_break_simulation(self):
  self.game.leave(1);bad=dict(money=-100,deliveries=True,x='oops',z=0,bag=[1000],angle=1e999,health='oops',driving='yes')
  self.store.db.execute('INSERT OR REPLACE INTO game_state VALUES(?,?)',(1,json.dumps(bad)))
  self.game.join(self.user);self.assertEqual(self.state()['money'],0);self.assertEqual(self.state()['bag'],[0]*5);self.assertFalse(self.state()['driving']);self.assertEqual(self.state()['health'],100.)
  self.now+=.2;self.game.tick(self.user,dict(seq=1));json.dumps(self.state(),allow_nan=False)
 def test_existing_valid_save_preserves_economy(self):
  self.state().update(money=850,deliveries=4,bag=[2,1,0,0,1],speed=12.,angle=720.)
  self.game.leave(1);self.game.join(self.user);self.assertEqual(self.state()['money'],850);self.assertEqual(self.state()['deliveries'],4);self.assertEqual(self.state()['bag'],[2,1,0,0,1]);self.assertEqual(self.state()['speed'],0);self.assertEqual(self.state()['angle'],0)
 def test_corrupt_non_object_save_recovers(self):
  self.game.leave(1);self.store.db.execute('INSERT OR REPLACE INTO game_state VALUES(1,?)',('[1,2,3]',));self.game.join(self.user);self.assertEqual(self.state()['money'],0)

 def test_server_advertises_version_eight(self):
  r=self.game.snapshot(self.user);self.assertEqual(r["protocol"],8);self.assertEqual(r["version"],"8.0")

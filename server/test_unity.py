import json,uuid,unittest
from server import Store,Failure

class UnityWorldTests(unittest.TestCase):
 def setUp(self):
  self.store=Store(':memory:');self.store.create('Unity_Test','Unity-test-password-2026','player');self.store.create('Second_Test','Second-test-password-2026','player')
  self.u=self.store.db.execute('SELECT * FROM users WHERE id=1').fetchone();self.other=self.store.db.execute('SELECT * FROM users WHERE id=2').fetchone()
  self.game=self.store.game;self.now=100.;self.game.clock=lambda:self.now;self.game.join(self.u);self.game.join(self.other)
 def tearDown(self):self.store.db.close()
 @property
 def state(self):return self.game.get(self.u)['data']
 def action(self,cmd,**extra):return self.game.action(self.u,dict(cmd=cmd,request_id=extra.pop('request_id',uuid.uuid4().hex),**extra))
 def enter(self):self.state.update(x=8,z=-20);self.action('enter_shop',place=0)
 def cashier(self):self.enter();self.state.update(x=14.8,z=-14,money=500)
 def test_entry_requires_proximity_and_walking(self):
  with self.assertRaises(Failure):self.action('enter_shop',place=0)
  self.state.update(x=8,z=-20,driving=True)
  with self.assertRaises(Failure):self.action('enter_shop',place=0)
  self.assertEqual(self.state['interior'],-1)
 def test_entry_and_exit(self):
  self.enter();self.assertEqual(self.state['interior'],0);self.assertEqual(self.state['x'],12.2)
  self.action('exit_shop');self.assertEqual(self.state['interior'],-1);self.assertEqual(self.state['x'],8)
 def test_distant_exit_rejected(self):
  self.enter();self.state.update(x=26,z=-14)
  with self.assertRaises(Failure):self.action('exit_shop')
  self.assertEqual(self.state['interior'],0)
 def test_car_forbidden_inside(self):
  self.enter()
  with self.assertRaises(Failure):self.action('car')
 def test_interior_visibility(self):
  self.enter();snapshot=self.game.snapshot(self.u);self.assertEqual(snapshot['online'],2);self.assertEqual(snapshot['players'],[])
  other=self.game.get(self.other)['data'];other.update(interior=0,x=14,z=-20,appearance=1)
  snapshot=self.game.snapshot(self.u);self.assertEqual(snapshot['players'][0]['appearance'],1)
 def test_inside_movement_shelf_collision_and_bounds(self):
  self.enter();self.state.update(x=16.7,z=-23)
  self.now+=.25;self.game.tick(self.u,dict(seq=1,mx=1,my=0,yaw=0));self.assertEqual(self.state['x'],16.7)
  self.assertFalse(self.game.clear_state(self.state,10,-20,.45));self.assertTrue(self.game.clear_state(self.state,26,-20,.45))
 def test_cart_atomic_payment_and_replay(self):
  self.cashier();request=uuid.uuid4().hex
  self.action('buy_cart',place=0,cart={'0':2,'1':1,'2':1},request_id=request)
  self.assertEqual(self.state['money'],410);self.assertEqual(self.state['bag'],[2,1,1,0,0])
  self.action('buy_cart',place=0,cart={'2':1,'1':1,'0':2},request_id=request);self.assertEqual(self.state['money'],410)
  with self.assertRaises(Failure):self.action('buy_cart',place=0,cart={'0':3},request_id=request)
 def test_cart_insufficient_funds_rolls_back_all_items(self):
  self.cashier();self.state['money']=40
  with self.assertRaises(Failure):self.action('buy_cart',place=0,cart={'0':1,'2':1})
  self.assertEqual(self.state['money'],40);self.assertEqual(self.state['bag'],[0]*5)
 def test_cart_capacity_rolls_back_all_items(self):
  self.cashier();self.state['bag'][1]=99
  with self.assertRaises(Failure):self.action('buy_cart',place=0,cart={'0':1,'1':1})
  self.assertEqual(self.state['money'],500);self.assertEqual(self.state['bag'],[0,99,0,0,0])
 def test_cart_requires_indoor_cashier(self):
  self.state.update(x=8,z=-20,money=500)
  with self.assertRaises(Failure):self.action('buy_cart',place=0,cart={'0':1})
  self.enter()
  with self.assertRaises(Failure):self.action('buy_cart',place=0,cart={'0':1})
 def test_cart_validation(self):
  self.cashier()
  for cart in ({},{'0':True},{'0':0},{'0':100},{'3':1},{'0':1.5},[],None):
   with self.subTest(cart=cart),self.assertRaises(Failure):self.action('buy_cart',place=0,cart=cart)
  self.assertEqual(self.state['money'],500)
 def test_appearance_persists_but_interior_resets_on_reconnect(self):
  self.enter();self.action('appearance',item=1);self.game.leave(1);s=self.game.join(self.u)['state']
  self.assertEqual(s['appearance'],1);self.assertEqual(s['interior'],-1);self.assertTrue(self.game.clear(s['x'],s['z'],.45))
 def test_invalid_appearance_and_reset(self):
  with self.assertRaises(Failure):self.action('appearance',item=2)
  self.enter();self.action('reset');self.assertEqual(self.state['interior'],-1)
 def test_cannot_forge_position_or_balance_in_cart(self):
  self.cashier();self.action('buy_cart',place=0,cart={'0':1},money=999999,x=90,z=90)
  self.assertEqual(self.state['money'],485);self.assertEqual(self.state['x'],14.8)

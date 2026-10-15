"""Authoritative multiplayer prototype: server time, movement, inventory and economy."""
import copy, hashlib, json, math, re, time
from server_errors import Failure
PLACES=[('АТБ',8,-20),('АПТЕКА',-8,-20),('НОВА ПОШТА',-8,20),("КАВ'ЯРНЯ",8,60),('АЗС',48,20),('ЛІКАРНЯ',-48,-20),('ЗУПИНКА',6.4,-8)]
PRICES=[15,25,35,40,100];ENERGY=[15,25,25,30,0];DEST=[(0,-42),(42,0),(0,42),(-42,0)]
class CityWorld:
 def __init__(self,db,clock=time.monotonic):
  self.db=db;self.clock=clock;self.active={}
  db.executescript('CREATE TABLE IF NOT EXISTS game_state(user_id INTEGER PRIMARY KEY REFERENCES users(id),state TEXT NOT NULL); CREATE TABLE IF NOT EXISTS game_actions(user_id INTEGER NOT NULL,request_id TEXT NOT NULL,digest TEXT NOT NULL,message TEXT NOT NULL,at INTEGER NOT NULL,PRIMARY KEY(user_id,request_id));')
 def default(self):return dict(x=0.,z=6.,car_x=5.,car_z=6.,angle=0.,speed=0.,heading=0.,interior=-1,appearance=0,driving=False,money=0,deliveries=0,mission=-1,bag=[0]*5,health=100.,energy=100.,fuel=100.)
 def restore(self,state,saved):
  """Validate persisted data before simulation; older valid save fields remain compatible."""
  if not isinstance(saved,dict):return
  for key in ('money','deliveries'):
   value=saved.get(key)
   if type(value) is int and 0<=value<=2147483647:state[key]=value
  for key in ('health','energy','fuel'):
   value=saved.get(key)
   if type(value) in (int,float) and math.isfinite(value):state[key]=max(0,min(100,value))
  for a,b,margin in (('x','z',.45),('car_x','car_z',1.8)):
   x,z=saved.get(a),saved.get(b)
   if type(x) in (int,float) and type(z) in (int,float) and math.isfinite(x) and math.isfinite(z) and self.clear(x,z,margin):state[a],state[b]=x,z
  for key in ('angle','heading'):
   value=saved.get(key)
   if type(value) in (int,float) and math.isfinite(value):state[key]=value%360
  if type(saved.get('driving')) is bool:state['driving']=saved['driving']
  value=saved.get('mission')
  if type(value) is int and -1<=value<len(DEST):state['mission']=value
  bag=saved.get('bag')
  if isinstance(bag,list) and len(bag)==5 and all(type(v) is int and 0<=v<=99 for v in bag):state['bag']=list(bag)
  appearance=saved.get('appearance')
  if type(appearance) is int and appearance in (0,1):state['appearance']=appearance
  # Interiors are transient: persisted indoor coordinates are rejected by street collision validation.
  state['interior']=-1
  # A restored vehicle always starts stationary, never with saved momentum.
  state['speed']=0.
 def get(self,u):
  uid=u['id']
  if uid not in self.active:
   row=self.db.execute('SELECT state FROM game_state WHERE user_id=?',(uid,)).fetchone();state=self.default();offset=(uid-1)%16;state.update(x=(offset%4)*1.5,z=6+(offset//4)*2,car_x=5+(offset%4)*2.5,car_z=6+(offset//4)*5)
   if row:
    try:self.restore(state,json.loads(row[0]))
    except (ValueError,TypeError):pass
   self.active[uid]=dict(data=state,name=u['username'],seen=self.clock(),step=self.clock(),seq=-1,saved=self.clock())
  return self.active[uid]
 def save(self,uid):
  if uid in self.active:self.db.execute('INSERT INTO game_state VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET state=excluded.state',(uid,json.dumps(self.active[uid]['data'],allow_nan=False,separators=(',',':'))))
 def leave(self,uid):
  self.save(uid);self.active.pop(uid,None)
 def prune(self):
  for uid in list(self.active):
   if self.clock()-self.active[uid]['seen']>60:self.leave(uid)
 def join(self,u):
  self.prune();p=self.get(u);p['seen']=self.clock();p['seq']=-1
  return self.snapshot(u,'Підключено до міста')
 def clear(self,x,z,margin):
  if abs(x)>98 or abs(z)>98:return False
  return not any(abs(x-cx)<10+margin and abs(z-cz)<10+margin for cx in (-60,-20,20,60) for cz in (-60,-20,20,60) if (cx,cz)!=(20,20))
 def clear_state(self,s,x,z,margin):
  if s.get('interior',-1)!=0:return self.clear(x,z,margin)
  if not 11+margin<=x<=29-margin or not -29+margin<=z<=-11-margin:return False
  if any(abs(x-cx)<.65+margin and abs(z-cz)<2+margin for cx,cz in ((18,-23),(22,-23),(18,-17),(22,-17))):return False
  return not (abs(x-13)<.6+margin and abs(z+14)<1.7+margin)
 def nearest(self,s):
  x,z=(s['car_x'],s['car_z']) if s['driving'] else (s['x'],s['z'])
  if s.get('interior',-1)==0:return 0 if math.hypot(x-14.8,z+14)<3 else -1
  return next((i for i,p in enumerate(PLACES) if math.hypot(x-p[1],z-p[2])<4),-1)
 def snapshot(self,u,message=''):
  p=self.get(u);s=copy.deepcopy(p['data']);s.update(id=u['id'],username=p['name'])
  visible=[];now=self.clock();online=0
  for uid,q in self.active.items():
   if now-q['seen']>10:continue
   online+=1
   if uid==u['id']:continue
   t=q['data'];x,z=(t['car_x'],t['car_z']) if t['driving'] else (t['x'],t['z'])
   px,pz=(s['car_x'],s['car_z']) if s['driving'] else (s['x'],s['z'])
   if t.get('interior',-1)==s.get('interior',-1) and math.hypot(x-px,z-pz)<=120:visible.append(dict(id=uid,username=q['name'],appearance=t.get('appearance',0),x=x,z=z,heading=t['angle'] if t['driving'] else t['heading'],driving=t['driving']))
  return dict(state=s,players=visible[:64],online=online,message=message,protocol=8,version='8.0')
 def tick(self,u,data):
  p=self.get(u);seq=data.get('seq')
  if type(seq) is not int or not 0<=seq<=2147483647 or seq<=p['seq']:raise Failure(409,'Порушено порядок команд.')
  axes=[]
  for name in ('mx','my','yaw'):
   v=data.get(name,0)
   if type(v) not in (int,float) or not math.isfinite(v):raise Failure(400,'Некоректне керування.')
   axes.append(v)
  mx,my,yaw=axes;mx=max(-1,min(1,mx));my=max(-1,min(1,my));length=max(1,math.hypot(mx,my));mx/=length;my/=length
  brake=data.get('brake',False);sprint=data.get('sprint',False)
  if type(brake) is not bool or type(sprint) is not bool:raise Failure(400,'Некоректний режим керування.')
  now=self.clock();dt=max(0,min(.25,now-p['step']));p.update(seq=seq,step=now,seen=now);s=p['data']
  if u['frozen']:mx=my=0;sprint=False;s['speed']=0
  if s['driving']:
   if brake:
    amount=20*dt;s['speed']=math.copysign(max(0,abs(s['speed'])-amount),s['speed'])
   else:s['speed']=max(-8,min(24,s['speed']+((-my*17 if s['fuel']>0 else 0)-s['speed']*1.3)*dt))
   s['fuel']=max(0,s['fuel']-abs(s['speed'])*dt*.01)
   s['angle']=(s['angle']-mx*min(70,abs(s['speed'])*8)*dt*(-1 if s['speed']<0 else 1))%360;r=math.radians(s['angle']);a=s['car_x']-math.sin(r)*s['speed']*dt;b=s['car_z']-math.cos(r)*s['speed']*dt
   if self.clear(a,b,1.8):s['car_x'],s['car_z']=a,b
   else:
    if abs(s['speed'])>8:s['health']=max(1,s['health']-abs(s['speed'])*.7)
    s['speed']=0
  else:
   if sprint and s['energy']>0 and abs(mx)+abs(my)>.1:s['energy']=max(0,s['energy']-dt)
   v=7 if sprint and s['energy']>0 else 3.5;dx=(mx*math.cos(yaw)+my*math.sin(yaw))*v*dt;dz=(-mx*math.sin(yaw)+my*math.cos(yaw))*v*dt
   if self.clear_state(s,s['x']+dx,s['z'],.45):s['x']+=dx
   if self.clear_state(s,s['x'],s['z']+dz,.45):s['z']+=dz
   if abs(dx)+abs(dz)>.0001:s['heading']=math.degrees(math.atan2(dx,dz))
  message=''
  if s.get('interior',-1)==-1 and 0<=s['mission']<4:
   px,pz=(s['car_x'],s['car_z']) if s['driving'] else (s['x'],s['z']);tx,tz=DEST[s['mission']]
   if math.hypot(px-tx,pz-tz)<3.5 and (not s['driving'] or abs(s['speed'])<=1.5):s['money']=min(2147483647,s['money']+250);s['deliveries']=min(2147483647,s['deliveries']+1);s['mission']=-1;message='Доставлено! +250 ₴';self.save(u['id'])
  if now-p['saved']>=2:self.save(u['id']);p['saved']=now
  return self.snapshot(u,message)
 def action(self,u,data):
  p=self.get(u);p['seen']=self.clock();s=p['data'];cmd=data.get('cmd');request=data.get('request_id')
  if not isinstance(request,str) or not re.fullmatch('[a-f0-9]{32}',request):raise Failure(400,'Потрібен ID операції.')
  if cmd not in ('car','job','cancel_job','buy','use','service','reset','enter_shop','exit_shop','appearance','buy_cart'):raise Failure(400,'Невідома дія.')
  if u['frozen']:raise Failure(403,'Акаунт заморожено адміністратором.')
  item=data.get('item',-1);place=data.get('place',-1)
  if type(item) is not int or type(place) is not int:raise Failure(400,'Некоректні параметри.')
  cart=data.get('cart')
  if cmd=='buy_cart' and (not isinstance(cart,dict) or not cart or any(k not in ('0','1','2') or type(v) is not int or not 1<=v<=99 for k,v in cart.items())):raise Failure(400,'Некоректний кошик.')
  identity=[cmd,item,place]+([sorted(cart.items())] if cmd=='buy_cart' else [])
  digest=hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest();old=self.db.execute('SELECT digest,message FROM game_actions WHERE user_id=? AND request_id=?',(u['id'],request)).fetchone()
  if old:
   if old[0]!=digest:raise Failure(409,'ID операції вже використаний.')
   return self.snapshot(u,old[1])
  before=copy.deepcopy(s);message=''
  try:
   self.db.execute('BEGIN IMMEDIATE')
   if cmd=='enter_shop':
    if s.get('interior',-1)!=-1 or place!=0 or self.nearest(s)!=0 or s['driving']:raise Failure(409,'Підійдіть пішки до входу АТБ.')
    s.update(interior=0,x=12.2,z=-20.,speed=0.);message='Ви увійшли до АТБ'
   elif cmd=='exit_shop':
    if s.get('interior',-1)!=0 or math.hypot(s['x']-12.2,s['z']+20)>=2.5:raise Failure(409,'Підійдіть до виходу.')
    s.update(interior=-1,x=8.,z=-20.,speed=0.);message='Ви вийшли з АТБ'
   elif cmd=='appearance':
    if item not in (0,1):raise Failure(400,'Невідомий персонаж.')
    s['appearance']=item;message='Персонажа збережено'
   elif cmd=='buy_cart':
    if s.get('interior',-1)!=0 or self.nearest(s)!=0 or place!=0 or s['driving']:raise Failure(409,'Підійдіть пішки до каси АТБ.')
    total=sum(PRICES[int(k)]*v for k,v in cart.items())
    if s['money']<total:raise Failure(409,'Недостатньо коштів.')
    if any(s['bag'][int(k)]+v>99 for k,v in cart.items()):raise Failure(409,'Рюкзак заповнений.')
    s['money']-=total
    for k,v in cart.items():s['bag'][int(k)]+=v
    message='Кошик оплачено та збережено сервером'
   elif cmd=='car':
    if s.get('interior',-1)!=-1:raise Failure(409,'Автомобіль залишився на вулиці.')
    if s['driving']:
     if abs(s['speed'])>1.5:raise Failure(409,'Спочатку зупиніть автомобіль.')
     angle=math.radians(s['angle']);candidates=[(s['car_x']+side*2.4*math.cos(angle),s['car_z']-side*2.4*math.sin(angle)) for side in (1,-1)]
     exit=next(((x,z) for x,z in candidates if self.clear(x,z,.5)),None)
     if exit is None:raise Failure(409,'Немає місця для виходу.')
     a,b=exit;s.update(driving=False,x=a,z=b,speed=0);message='Ви вийшли з авто'
    else:
     if math.hypot(s['x']-s['car_x'],s['z']-s['car_z'])>=4:raise Failure(409,'Підійдіть до свого авто.')
     s['driving']=True;message='Ви сіли в авто'
   elif cmd=='job':
    if s['mission']>=0:raise Failure(409,'Спочатку завершіть доставку.')
    s['mission']=s['deliveries']%4;message='Прямуйте до жовтого маркера'
   elif cmd=='cancel_job':
    if s['mission']<0:raise Failure(409,'Активної доставки немає.')
    s['mission']=-1;message='Доставку скасовано без винагороди'
   elif cmd=='buy':
    sells=(place==0 and 0<=item<=2) or (place==1 and item==4) or (place==3 and item==3)
    if not sells or self.nearest(s)!=place or s['driving']:raise Failure(409,'Підійдіть пішки до входу потрібної крамниці.')
    if s['money']<PRICES[item]:raise Failure(409,'Недостатньо коштів.')
    if s['bag'][item]>=99:raise Failure(409,'Рюкзак заповнений.')
    s['money']-=PRICES[item];s['bag'][item]+=1;message='Покупку збережено сервером'
   elif cmd=='use':
    if not 0<=item<5 or s['bag'][item]<=0:raise Failure(409,'Предмета немає в рюкзаку.')
    s['bag'][item]-=1;s['energy']=min(100,s['energy']+ENERGY[item]);s['health']=min(100,s['health']+(50 if item==4 else 0));message='Предмет використано'
   elif cmd=='service':
    if self.nearest(s)!=place or place not in (4,5):raise Failure(409,'Підійдіть до об’єкта.')
    if place==4 and not s['driving']:raise Failure(409,'Під’їдьте на авто.')
    field='fuel' if place==4 else 'health';cost=100 if place==4 else 60
    if s[field]>=99:raise Failure(409,'Послуга зараз не потрібна.')
    if s['money']<cost:raise Failure(409,'Недостатньо коштів.')
    s['money']-=cost;s[field]=100;message='Послугу надано'
   else:s.update(x=0.,z=6.,car_x=5.,car_z=6.,interior=-1,driving=False,speed=0.,angle=0.);message='Позицію відновлено'
   self.save(u['id']);self.db.execute('INSERT INTO game_actions VALUES(?,?,?,?,?)',(u['id'],request,digest,message,int(time.time())));self.db.execute('COMMIT')
  except Exception:self.db.execute('ROLLBACK');p['data']=before;raise
  return self.snapshot(u,message)

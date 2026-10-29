import os, tempfile, importlib
from fastapi.testclient import TestClient

def setup_module():
    global mod, client, tmp
    tmp=tempfile.TemporaryDirectory()
    os.environ['DATABASE_URL']='sqlite:///'+tmp.name+'/test.db'
    os.environ['JWT_SECRET']='test-secret-key-that-is-long-enough-1234567890'
    os.environ.pop('RENDER',None); os.environ.pop('ENVIRONMENT',None)
    import app.main as m
    mod=m
    # test module's engine is created once against test DB
    client=TestClient(mod.app)
    with mod.SessionLocal() as db:
        if not db.query(mod.BarTable).count():
            db.add_all([mod.BarTable(name='A1',capacity=4,status='空闲'),mod.BarTable(name='A2',capacity=6,status='空闲')]); db.commit()
        if not db.query(mod.Drink).count(): db.add(mod.Drink(name='测试啤酒',stock=12)); db.commit()
        if not db.query(mod.User).filter_by(phone='13900000000').first():
            u=mod.User(phone='13900000000',nickname='测试管理员',password_hash=mod.pw_hash('AdminPassword123!'),role='admin'); db.add(u); db.commit()
def teardown_module(): tmp.cleanup()
def auth(phone,pw): return {'Authorization':'Bearer '+client.post('/api/auth/login',json={'phone':phone,'password':pw}).json()['token']}
def test_register_login_phone_password_no_sms():
    r=client.post('/api/auth/register',json={'phone':'13812345678','nickname':'测试顾客','password':'CustomerPass123!'}); assert r.status_code==200
    assert r.json()['user']['phone']=='13812345678'
    assert client.post('/api/auth/register',json={'phone':'13812345678','nickname':'重复','password':'CustomerPass123!'}).status_code==409
    assert client.post('/api/auth/login',json={'phone':'13812345678','password':'wrong-password'}).status_code==401
    assert client.get('/health').json()['ok'] is True
def test_admin_can_update_table_and_drink():
    h=auth('13900000000','AdminPassword123!')
    tables=client.get('/api/tables',headers=h).json(); t=tables[0]
    r=client.patch(f"/api/admin/tables/{t['id']}",headers=h,json={'status':'使用中','capacity':4}); assert r.status_code==200 and r.json()['status']=='使用中'
    d=client.get('/api/drinks',headers=h).json()[0]
    r=client.patch(f"/api/admin/drinks/{d['id']}",headers=h,json={'name':'改名啤酒','stock':27,'active':True}); assert r.status_code==200 and r.json()['name']=='改名啤酒' and r.json()['stock']==27
def test_offline_recharge_only_admin():
    h=auth('13900000000','AdminPassword123!')
    r=client.post('/api/auth/register',json={'phone':'13812345679','nickname':'储值顾客','password':'CustomerPass123!'}); uid=r.json()['user']['id']
    r=client.post(f'/api/admin/wallets/{uid}/confirm-offline-recharge',headers=h,json={'amount':'50.00','method':'现金','remark':'测试确认'}); assert r.status_code==200 and r.json()['balance']=='50.00'
    customer={'Authorization':'Bearer '+r.json().get('token','')}
    assert client.get('/api/admin/wallets',headers=customer).status_code in (401,403)
def test_customer_can_book_without_deposit_and_cannot_administer():
    reg=client.post('/api/auth/register',json={'phone':'13812345680','nickname':'订桌顾客','password':'CustomerPass123!'})
    assert reg.status_code==200
    h={'Authorization':'Bearer '+reg.json()['token']}
    free=[t for t in client.get('/api/tables',headers=h).json() if t['status']=='空闲']
    from datetime import datetime, timedelta, timezone
    when=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    b=client.post('/api/bookings',headers=h,json={'table_id':free[0]['id'],'booking_time':when,'party_size':2})
    assert b.status_code==200
    assert 'deposit' not in b.json() and b.json()['status']=='待确认'
    assert client.patch(f"/api/admin/tables/{free[0]['id']}",headers=h,json={'status':'使用中'}).status_code==403
def test_duplicate_booking_same_table_and_time_rejected():
    reg=client.post('/api/auth/register',json={'phone':'13812345681','nickname':'第二位订桌顾客','password':'CustomerPass123!'})
    h={'Authorization':'Bearer '+reg.json()['token']}
    free=next(t for t in client.get('/api/tables',headers=h).json() if t['status']=='空闲')
    from datetime import datetime, timedelta, timezone
    when=(datetime.now(timezone.utc)+timedelta(days=2)).replace(microsecond=0).isoformat()
    payload={'table_id':free['id'],'booking_time':when,'party_size':2}
    assert client.post('/api/bookings',headers=h,json=payload).status_code==200
    assert client.post('/api/bookings',headers=h,json=payload).status_code==409

import os, re, secrets, hashlib, hmac
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Integer, Boolean, DateTime, ForeignKey, Numeric, Text, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
import jwt

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./webbar-test.db')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql+psycopg://', 1)
elif DATABASE_URL.startswith('postgresql://') and '+psycopg' not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace('postgresql://', 'postgresql+psycopg://', 1)
engine_kwargs = {'pool_pre_ping': True}
if DATABASE_URL.startswith('sqlite'):
    engine_kwargs['connect_args'] = {'check_same_thread': False}
engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
SECRET_KEY = os.getenv('JWT_SECRET', '')
if len(SECRET_KEY) < 32:
    # Refuse insecure default for hosted environments. Local tests can set JWT_SECRET.
    if os.getenv('RENDER') or os.getenv('ENVIRONMENT') == 'production':
        raise RuntimeError('Set JWT_SECRET to a random value of at least 32 characters')
    SECRET_KEY = 'local-test-only-change-this-secret-key-123456789'
ALGORITHM = 'HS256'

class Base(DeclarativeBase): pass
class User(Base):
    __tablename__='users'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    phone: Mapped[str]=mapped_column(String(32), unique=True, index=True)
    nickname: Mapped[str]=mapped_column(String(80))
    password_hash: Mapped[str]=mapped_column(String(256))
    role: Mapped[str]=mapped_column(String(20), default='customer')
    active: Mapped[bool]=mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
class BarTable(Base):
    __tablename__='bar_tables'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    name: Mapped[str]=mapped_column(String(40), unique=True)
    capacity: Mapped[int]=mapped_column(Integer, default=4)
    status: Mapped[str]=mapped_column(String(20), default='空闲')
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
class Drink(Base):
    __tablename__='drinks'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    name: Mapped[str]=mapped_column(String(120), index=True)
    stock: Mapped[int]=mapped_column(Integer, default=0)
    active: Mapped[bool]=mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
class Booking(Base):
    __tablename__='bookings'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    table_id: Mapped[int]=mapped_column(ForeignKey('bar_tables.id'))
    booking_time: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    party_size: Mapped[int]=mapped_column(Integer)
    status: Mapped[str]=mapped_column(String(20), default='待确认')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
class Wallet(Base):
    __tablename__='wallets'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'), unique=True)
    balance: Mapped[Decimal]=mapped_column(Numeric(12,2), default=Decimal('0.00'))
class WalletTxn(Base):
    __tablename__='wallet_transactions'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    wallet_id: Mapped[int]=mapped_column(ForeignKey('wallets.id'))
    amount: Mapped[Decimal]=mapped_column(Numeric(12,2))
    kind: Mapped[str]=mapped_column(String(40))
    method: Mapped[str]=mapped_column(String(40), default='线下人工确认')
    remark: Mapped[str]=mapped_column(Text, default='')
    operator_id: Mapped[Optional[int]]=mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
class Audit(Base):
    __tablename__='audit_logs'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    actor_id: Mapped[Optional[int]]=mapped_column(Integer, nullable=True)
    action: Mapped[str]=mapped_column(String(80))
    entity: Mapped[str]=mapped_column(String(80))
    entity_id: Mapped[Optional[int]]=mapped_column(Integer, nullable=True)
    detail: Mapped[str]=mapped_column(Text, default='')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(engine)
@asynccontextmanager
async def lifespan(app):
    bootstrap_admin_and_defaults()
    yield

app=FastAPI(title='Web Bar API', version='1.0.0', lifespan=lifespan)
origins=[x.strip() for x in os.getenv('CORS_ORIGINS','*').split(',') if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=['GET','POST','PATCH','DELETE','OPTIONS'], allow_headers=['Authorization','Content-Type'])

def get_db():
    db=SessionLocal()
    try: yield db
    finally: db.close()

def pw_hash(password:str)->str:
    salt=secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex()+':'+digest.hex()
def pw_verify(password:str, stored:str)->bool:
    try:
        salt_hex,digest_hex=stored.split(':',1); salt=bytes.fromhex(salt_hex)
        actual=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1).hex()
        return hmac.compare_digest(actual,digest_hex)
    except Exception: return False

def token_for(user:User):
    now=datetime.now(timezone.utc)
    return jwt.encode({'sub':str(user.id),'role':user.role,'iat':now,'exp':now+timedelta(hours=12)},SECRET_KEY,algorithm=ALGORITHM)
def current_user(authorization: str=Header(default=''), db:Session=Depends(get_db)):
    if not authorization.startswith('Bearer '): raise HTTPException(401,'请先登录')
    try: payload=jwt.decode(authorization[7:],SECRET_KEY,algorithms=[ALGORITHM]); uid=int(payload['sub'])
    except Exception: raise HTTPException(401,'登录已过期，请重新登录')
    user=db.get(User,uid)
    if not user or not user.active: raise HTTPException(401,'账号不可用')
    return user
def require_admin(user:User=Depends(current_user)):
    if user.role!='admin': raise HTTPException(403,'仅管理员可操作')
    return user
def audit(db, actor, action, entity, entity_id=None, detail=''):
    db.add(Audit(actor_id=actor.id if actor else None, action=action, entity=entity, entity_id=entity_id, detail=detail[:2000]))

def serialize_user(u): return {'id':u.id,'phone':u.phone,'nickname':u.nickname,'role':u.role}
def serialize_table(t): return {'id':t.id,'name':t.name,'capacity':t.capacity,'status':t.status,'updated_at':t.updated_at.isoformat() if t.updated_at else None}
def serialize_drink(d): return {'id':d.id,'name':d.name,'stock':d.stock,'active':d.active,'updated_at':d.updated_at.isoformat() if d.updated_at else None}
def serialize_booking(b, db):
    t=db.get(BarTable,b.table_id); u=db.get(User,b.user_id)
    return {'id':b.id,'customer':u.nickname if u else '', 'phone':u.phone if u else '', 'table_id':b.table_id,'table_name':t.name if t else '', 'booking_time':b.booking_time.isoformat(),'party_size':b.party_size,'status':b.status,'created_at':b.created_at.isoformat()}

class RegisterIn(BaseModel): phone:str=Field(min_length=6,max_length=32); nickname:str=Field(min_length=1,max_length=80); password:str=Field(min_length=10,max_length=128)
class LoginIn(BaseModel): phone:str=Field(min_length=6,max_length=32); password:str=Field(min_length=1,max_length=128)
class BookingIn(BaseModel): table_id:int; booking_time:datetime; party_size:int=Field(ge=1,le=30)
class TableUpdate(BaseModel): status:str; capacity:Optional[int]=Field(default=None,ge=1,le=100)
class DrinkIn(BaseModel): name:str=Field(min_length=1,max_length=120); stock:int=Field(ge=0,le=1000000); active:bool=True
class RechargeIn(BaseModel): amount:Decimal=Field(gt=0,le=Decimal('100000')); method:str=Field(default='线下人工确认',max_length=40); remark:str=Field(default='',max_length=500)

@app.get('/health')
def health(db:Session=Depends(get_db)):
    db.execute(select(func.count()).select_from(User)).scalar_one()
    return {'ok':True,'database':'connected','time':datetime.now(timezone.utc).isoformat()}

@app.post('/api/auth/register')
def register(data:RegisterIn, db:Session=Depends(get_db)):
    phone=re.sub(r'[\s-]','',data.phone)
    if not re.fullmatch(r'\+?[0-9]{6,20}',phone): raise HTTPException(400,'手机号格式不正确')
    if db.scalar(select(User).where(User.phone==phone)): raise HTTPException(409,'该手机号已注册')
    user=User(phone=phone,nickname=data.nickname.strip(),password_hash=pw_hash(data.password),role='customer')
    db.add(user); db.flush(); db.add(Wallet(user_id=user.id)); audit(db,user,'register','user',user.id,'customer registration'); db.commit()
    return {'token':token_for(user),'user':serialize_user(user)}
@app.post('/api/auth/login')
def login(data:LoginIn, db:Session=Depends(get_db)):
    phone=re.sub(r'[\s-]','',data.phone); user=db.scalar(select(User).where(User.phone==phone))
    if not user or not user.active or not pw_verify(data.password,user.password_hash): raise HTTPException(401,'手机号或密码错误')
    return {'token':token_for(user),'user':serialize_user(user)}
@app.get('/api/me')
def me(user:User=Depends(current_user),db:Session=Depends(get_db)):
    wallet=db.scalar(select(Wallet).where(Wallet.user_id==user.id))
    return {**serialize_user(user),'wallet_balance':str(wallet.balance if wallet else 0)}

@app.get('/api/tables')
def tables(db:Session=Depends(get_db), user:User=Depends(current_user)):
    return [serialize_table(t) for t in db.scalars(select(BarTable).order_by(BarTable.name)).all()]
@app.patch('/api/admin/tables/{table_id}')
def update_table(table_id:int,data:TableUpdate,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    if data.status not in ['空闲','使用中','已预订','暂停使用']: raise HTTPException(400,'桌位状态不合法')
    t=db.get(BarTable,table_id)
    if not t: raise HTTPException(404,'桌位不存在')
    before=f'{t.status}/{t.capacity}'; t.status=data.status
    if data.capacity is not None: t.capacity=data.capacity
    audit(db,admin,'table_update','table',t.id,f'{before} -> {t.status}/{t.capacity}'); db.commit(); db.refresh(t); return serialize_table(t)
@app.post('/api/bookings')
def create_booking(data:BookingIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if user.role!='customer': raise HTTPException(403,'顾客账号才能订桌')
    if data.booking_time.tzinfo is None: raise HTTPException(400,'预约时间必须包含时区')
    if data.booking_time <= datetime.now(timezone.utc): raise HTTPException(400,'预约时间必须在未来')
    t=db.scalar(select(BarTable).where(BarTable.id==data.table_id).with_for_update())
    if not t or t.status in ['使用中','已预订','暂停使用']: raise HTTPException(409,'该桌位当前不可预订')
    if data.party_size>t.capacity: raise HTTPException(400,'人数超过桌位容量')
    # Prevent duplicate bookings for the same table and exact time. Table status is a live floor-status field managed separately by staff.
    conflict=db.scalar(select(Booking).where(Booking.table_id==t.id, Booking.booking_time==data.booking_time, Booking.status.notin_(['取消','爽约'])).with_for_update())
    if conflict: raise HTTPException(409,'该桌位在此时间已有订桌记录')
    b=Booking(user_id=user.id,table_id=t.id,booking_time=data.booking_time,party_size=data.party_size,status='待确认')
    db.add(b); db.flush(); audit(db,user,'booking_create','booking',b.id,'No deposit collected'); db.commit(); db.refresh(b); return serialize_booking(b,db)
@app.get('/api/bookings')
def my_bookings(db:Session=Depends(get_db),user:User=Depends(current_user)):
    stmt=select(Booking).where(Booking.user_id==user.id) if user.role=='customer' else select(Booking)
    return [serialize_booking(b,db) for b in db.scalars(stmt.order_by(Booking.id.desc())).all()]
@app.patch('/api/admin/bookings/{booking_id}')
def update_booking(booking_id:int,status:str,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    if status not in ['待确认','已确认','已到店','取消','爽约']: raise HTTPException(400,'订桌状态不合法')
    b=db.get(Booking,booking_id)
    if not b: raise HTTPException(404,'订桌记录不存在')
    old=b.status; b.status=status
    audit(db,admin,'booking_status','booking',b.id,f'{old} -> {status}'); db.commit(); db.refresh(b); return serialize_booking(b,db)

@app.get('/api/drinks')
def drinks(db:Session=Depends(get_db),user:User=Depends(current_user)):
    stmt=select(Drink).order_by(Drink.name)
    if user.role!='admin': stmt=stmt.where(Drink.active==True)
    return [serialize_drink(d) for d in db.scalars(stmt).all()]
@app.post('/api/admin/drinks')
def create_drink(data:DrinkIn,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    d=Drink(name=data.name.strip(),stock=data.stock,active=data.active); db.add(d); db.flush(); audit(db,admin,'drink_create','drink',d.id,f'{d.name}, stock={d.stock}'); db.commit(); db.refresh(d); return serialize_drink(d)
@app.patch('/api/admin/drinks/{drink_id}')
def update_drink(drink_id:int,data:DrinkIn,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    d=db.get(Drink,drink_id)
    if not d: raise HTTPException(404,'酒水项目不存在')
    old=f'{d.name}/{d.stock}/{d.active}'; d.name=data.name.strip(); d.stock=data.stock; d.active=data.active
    audit(db,admin,'drink_update','drink',d.id,f'{old} -> {d.name}/{d.stock}/{d.active}'); db.commit(); db.refresh(d); return serialize_drink(d)
@app.delete('/api/admin/drinks/{drink_id}')
def delete_drink(drink_id:int,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    d=db.get(Drink,drink_id)
    if not d: raise HTTPException(404,'酒水项目不存在')
    d.active=False; audit(db,admin,'drink_deactivate','drink',d.id,d.name); db.commit(); return {'ok':True}

@app.get('/api/admin/customers')
def customers(db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    return [{'id':u.id,'phone':u.phone,'nickname':u.nickname,'active':u.active,'created_at':u.created_at.isoformat()} for u in db.scalars(select(User).where(User.role=='customer').order_by(User.id.desc())).all()]
@app.get('/api/admin/wallets')
def wallets(db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    out=[]
    for u in db.scalars(select(User).where(User.role=='customer').order_by(User.id.desc())).all():
        w=db.scalar(select(Wallet).where(Wallet.user_id==u.id))
        out.append({'user_id':u.id,'nickname':u.nickname,'phone':u.phone,'balance':str(w.balance if w else 0)})
    return out
@app.post('/api/admin/wallets/{user_id}/confirm-offline-recharge')
def confirm_recharge(user_id:int,data:RechargeIn,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    # This endpoint must only be called after a human has verified the money arrived.
    w=db.scalar(select(Wallet).where(Wallet.user_id==user_id))
    if not w: raise HTTPException(404,'钱包不存在')
    w.balance=Decimal(w.balance)+data.amount
    tx=WalletTxn(wallet_id=w.id,amount=data.amount,kind='线下充值确认',method=data.method,remark=data.remark,operator_id=admin.id)
    db.add(tx); db.flush(); audit(db,admin,'offline_recharge_confirm','wallet',w.id,f'amount={data.amount}; method={data.method}; txn={tx.id}'); db.commit(); db.refresh(w)
    return {'user_id':user_id,'balance':str(w.balance),'transaction_id':tx.id}
@app.get('/api/wallet/transactions')
def wallet_transactions(db:Session=Depends(get_db),user:User=Depends(current_user)):
    w=db.scalar(select(Wallet).where(Wallet.user_id==user.id))
    if not w: return []
    return [{'id':x.id,'amount':str(x.amount),'kind':x.kind,'method':x.method,'remark':x.remark,'created_at':x.created_at.isoformat()} for x in db.scalars(select(WalletTxn).where(WalletTxn.wallet_id==w.id).order_by(WalletTxn.id.desc())).all()]
@app.get('/api/admin/audit-logs')
def audit_logs(db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    return [{'id':a.id,'actor_id':a.actor_id,'action':a.action,'entity':a.entity,'entity_id':a.entity_id,'detail':a.detail,'created_at':a.created_at.isoformat()} for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(300)).all()]

def bootstrap_admin_and_defaults():
    phone=os.getenv('ADMIN_PHONE',''); password=os.getenv('ADMIN_PASSWORD',''); nickname=os.getenv('ADMIN_NICKNAME','系统管理员')
    if phone and password and len(password)<12: raise RuntimeError('ADMIN_PASSWORD must be at least 12 characters')
    with SessionLocal() as db:
        # Seed starter tables and editable inventory only when the tables are empty.
        if db.scalar(select(func.count()).select_from(BarTable)) == 0:
            db.add_all([BarTable(name=f'O{i}',capacity=4,status='空闲') for i in range(1,6)] + [BarTable(name=f'L{i}',capacity=8,status='空闲') for i in range(1,6)])
        if db.scalar(select(func.count()).select_from(Drink)) == 0:
            db.add_all([Drink(name='干红葡萄酒',stock=0),Drink(name='威士忌',stock=0),Drink(name='啤酒',stock=0)])
        if phone and password:
            existing=db.scalar(select(User).where(User.phone==phone))
            if not existing:
                u=User(phone=phone,nickname=nickname,password_hash=pw_hash(password),role='admin'); db.add(u)
            elif existing.role!='admin': raise RuntimeError('ADMIN_PHONE already belongs to a non-admin user')
        db.commit()

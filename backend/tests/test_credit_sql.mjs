// Offline SQL integration checks using an in-memory PostgreSQL test runtime.
// No credentials, network, live database, app import or paid service calls.
// node tests/test_credit_sql.mjs <absolute-path-to-pglite/dist/index.js>
// Test-only runtime download is separate; no application dependency is added.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const backend=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const runtime=process.argv[2];
if(!runtime)throw new Error('Provide the local PostgreSQL test runtime path.');
const { PGlite }=await import(pathToFileURL(path.resolve(runtime)).href);
const db=new PGlite();
let passed=0;
const uid='00000000-0000-4000-8000-000000000001';
let sequence=500;
const id=()=>`00000000-0000-4000-8000-${String(++sequence).padStart(12,'0')}`;
const one=async(sql,params=[]) => (await db.query(sql,params)).rows[0];
const call=async(name,args)=> (await one(`SELECT public.${name}(${args.map((_,i)=>'$'+(i+1)).join(',')}) AS result`,args)).result;
async function check(name,run){await run();passed++;console.log('PASS '+name);}
async function expectError(sql,args=[]){await assert.rejects(db.query(sql,args));}
const balances=async()=> await one('SELECT subscription_credits AS subscription,credits AS permanent FROM public.profiles WHERE id=$1',[uid]);
async function reset(sub=5,perm=10){
  await db.exec('TRUNCATE public.lisan_credit_operations,public.lisan_credit_pack_receipts,public.credit_orders,public.credit_spends;');
  await db.query('UPDATE public.profiles SET subscription_credits=$1,credits=$2 WHERE id=$3',[sub,perm,uid]);
}
const operations=await fs.readFile(path.join(backend,'sql/2026-10-credit-operations.sql'),'utf8');
const debit=await fs.readFile(path.join(backend,'sql/2026-10-atomic-credit-debit.sql'),'utf8');
const oldSQL=await fs.readFile(path.join(backend,'tests/fixtures/billing_rpc_definitions.sql'),'utf8');
const oldBodies=async()=> (await db.query("SELECT proname,md5(prosrc) AS body FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND proname IN ('add_credits','deduct_credits','deduct_subscription_credits') ORDER BY proname")).rows;
try {
  console.log('Local runtime: '+(await one('SELECT version() AS version')).version);
  await db.exec(`
    CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;
    CREATE TABLE public.profiles(id uuid PRIMARY KEY,credits integer,subscription_credits integer);
    CREATE TABLE public.credit_orders(session_id text PRIMARY KEY,uid uuid REFERENCES public.profiles(id),credits integer);
    CREATE TABLE public.credit_spends(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      uid uuid REFERENCES public.profiles(id),action text,credits integer,job_id uuid,generated_seconds numeric,
      created_at timestamptz DEFAULT clock_timestamp());
  `);
  await db.query('INSERT INTO public.profiles VALUES($1,10,5)',[uid]);
  await db.exec(oldSQL);
  const before=await oldBodies();
  await check('both drafts install and may be rerun',async()=>{
    await db.exec(operations);await db.exec(debit);
    await db.exec(operations);await db.exec(debit);
    assert.deepEqual(await oldBodies(),before);
    assert.deepEqual(await call('lisan_billing_ready',[]),{version:1,debit:true,pack:true,refund:true,cancel:true});
  });
  await check('all new functions are denied to browsers and executable by server',async()=>{
    const rows=(await db.query(`SELECT proname,
      has_function_privilege('anon',p.oid,'EXECUTE') AS anon,
      has_function_privilege('authenticated',p.oid,'EXECUTE') AS member,
      has_function_privilege('service_role',p.oid,'EXECUTE') AS server
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND proname LIKE 'lisan_%'`)).rows;
    assert.equal(rows.length,8);
    for(const row of rows){assert.equal(row.anon,false);assert.equal(row.member,false);assert.equal(row.server,true);}
    await db.exec('SET ROLE anon');
    try {await expectError('SELECT public.lisan_billing_ready()');}
    finally {await db.exec('RESET ROLE');}
  });
  await check('server-role debit works with security definer and locked-down tables',async()=>{
    await reset();await db.exec('SET ROLE service_role');
    try {assert.equal((await call('lisan_atomic_debit',[id(),uid,1])).status,'done');}
    finally {await db.exec('RESET ROLE');}
  });
  await check('combined debit, repeat ID and insufficient balance are exact',async()=>{
    await reset();const op=id();const r=await call('lisan_atomic_debit',[op,uid,8]);
    assert.equal(r.taken_subscription,5);assert.equal(r.taken_permanent,3);
    assert.deepEqual(await balances(),{subscription:0,permanent:7});
    assert.equal((await call('lisan_atomic_debit',[op,uid,8])).replayed,true);
    assert.equal((await one('SELECT count(*)::int AS n FROM public.credit_spends')).n,1);
    const fail=await call('lisan_atomic_debit',[id(),uid,8]);
    assert.equal(fail.reason,'insufficient');assert.equal(fail.status,'failed');
    assert.deepEqual(await balances(),{subscription:0,permanent:7});
  });
  await check('invalid zero, negative and null debit never changes a balance',async()=>{
    await reset();for(const amount of [0,-1,null])await expectError('SELECT public.lisan_atomic_debit($1,$2,$3)',[id(),uid,amount]);
    assert.deepEqual(await balances(),{subscription:5,permanent:10});
    assert.equal((await one('SELECT count(*)::int AS n FROM public.lisan_credit_operations')).n,0);
  });
  await check('unknown user is not a confirmed debit',async()=>{
    const r=await call('lisan_atomic_debit',[id(),'00000000-0000-4000-8000-000000000099',1]);
    assert.equal(r.status,'failed');assert.equal(r.reason,'missing_profile');
  });
  await check('ID reuse with changed user, amount or work is refused',async()=>{
    await reset();const op=id();await call('lisan_atomic_debit',[op,uid,5,'clone']);
    for(const args of [[op,uid,6,'clone'],[op,uid,5,'generate'],[op,'00000000-0000-4000-8000-000000000099',5,'clone']])
      await expectError('SELECT public.lisan_atomic_debit($1,$2,$3,$4)',args);
    assert.deepEqual(await balances(),{subscription:0,permanent:10});
  });
  await check('history failure rolls back BOTH buckets and operation',async()=>{
    await reset();await db.exec(`CREATE FUNCTION public.test_history_fault() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'synthetic history failure'; END $$;
      CREATE TRIGGER test_history_fault BEFORE INSERT ON public.credit_spends FOR EACH ROW EXECUTE FUNCTION public.test_history_fault();`);
    try {await expectError('SELECT public.lisan_atomic_debit($1,$2,$3)',[id(),uid,8]);}
    finally {await db.exec('DROP TRIGGER test_history_fault ON public.credit_spends; DROP FUNCTION public.test_history_fault();');}
    assert.deepEqual(await balances(),{subscription:5,permanent:10});
    assert.equal((await one('SELECT count(*)::int AS n FROM public.lisan_credit_operations')).n,0);
  });
  await check('nonfinite duration is refused without a debit',async()=>{
    await reset();for(const duration of ['NaN','Infinity','-Infinity',-1])
      await expectError('SELECT public.lisan_atomic_debit($1,$2,$3,$4,$5,$6)',[id(),uid,1,'generate',null,duration]);
    assert.deepEqual(await balances(),{subscription:5,permanent:10});
  });
  await check('failed pack receipt/grant rolls back and same receipt is recoverable',async()=>{
    await reset();await db.exec(`CREATE FUNCTION public.test_pack_fault() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'synthetic pack failure'; END $$;
      CREATE TRIGGER test_pack_fault BEFORE INSERT ON public.credit_orders FOR EACH ROW EXECUTE FUNCTION public.test_pack_fault();`);
    try {await expectError('SELECT public.lisan_fulfill_pack($1,$2,$3)',[uid,'pack',100]);}
    finally {await db.exec('DROP TRIGGER test_pack_fault ON public.credit_orders; DROP FUNCTION public.test_pack_fault();');}
    assert.equal((await one('SELECT count(*)::int AS n FROM public.lisan_credit_pack_receipts')).n,0);
    assert.equal((await call('lisan_fulfill_pack',[uid,'pack',100])).status,'done');
    assert.equal((await call('lisan_fulfill_pack',[uid,'pack',100])).status,'already_fulfilled');
    assert.deepEqual(await balances(),{subscription:5,permanent:110});
  });
  await check('changed pack details and legacy ambiguous receipts do not grant',async()=>{
    await reset();await call('lisan_fulfill_pack',[uid,'pack',100]);
    await expectError('SELECT public.lisan_fulfill_pack($1,$2,$3)',[uid,'pack',200]);
    await db.query('INSERT INTO public.credit_orders VALUES($1,$2,$3)',['legacy',uid,100]);
    assert.equal((await call('lisan_fulfill_pack',[uid,'legacy',100])).status,'legacy_review');
    assert.deepEqual(await balances(),{subscription:5,permanent:110});
  });
  await check('begin/finish record durable IDs and refuse a changed settlement',async()=>{
    await reset();const op=id();assert.equal((await call('lisan_credit_begin',[op,uid,'debit',5])).status,'pending');
    assert.deepEqual(await balances(),{subscription:5,permanent:10});
    await call('lisan_atomic_debit',[op,uid,5,'clone']);
    assert.equal((await call('lisan_credit_begin',[op,uid,'debit',5])).status,'done');
    await call('lisan_credit_finish',[op,'failed',5]);
    await call('lisan_credit_finish',[op,'failed',5]);
    await expectError('SELECT public.lisan_credit_finish($1,$2,$3)',[op,'delivered',0]);
  });
  await check('full refund restores original buckets, history and ID are once only',async()=>{
    await reset(3,10);const op=id(),refund=id();await call('lisan_atomic_debit',[op,uid,5,'clone']);
    await call('lisan_credit_finish',[op,'failed',5]);
    const r=await call('lisan_credit_refund',[refund,uid,5,op,'proportional_subscription_up']);
    assert.equal(r.taken_subscription,3);assert.equal(r.taken_permanent,2);
    assert.equal((await call('lisan_credit_refund',[refund,uid,5,op,'proportional_subscription_up'])).replayed,true);
    assert.deepEqual(await balances(),{subscription:3,permanent:10});
    assert.deepEqual((await db.query('SELECT credits FROM public.credit_spends ORDER BY id')).rows,[{credits:5},{credits:-5}]);
    await expectError('SELECT public.lisan_credit_refund($1,$2,$3,$4,$5)',[id(),uid,1,op,'proportional_subscription_up']);
  });
  await check('partial refunds use cumulative integer split and cannot exceed settlement',async()=>{
    await reset(3,10);const op=id();await call('lisan_atomic_debit',[op,uid,5,'clone']);
    await call('lisan_credit_finish',[op,'partial',2]);
    const r=await call('lisan_credit_refund',[id(),uid,2,op,'proportional_subscription_up']);
    assert.equal(r.taken_subscription,2);assert.equal(r.taken_permanent,0);
    await expectError('SELECT public.lisan_credit_refund($1,$2,$3,$4,$5)',[id(),uid,1,op,'proportional_subscription_up']);
    assert.deepEqual(await balances(),{subscription:2,permanent:8});
  });
  await check('a refund cannot precede a confirmed debit and recorded outcome',async()=>{
    await reset();const op=id();await call('lisan_credit_begin',[op,uid,'debit',5]);
    await expectError('SELECT public.lisan_credit_refund($1,$2,$3,$4,$5)',[id(),uid,5,op,'proportional_subscription_up']);
    await call('lisan_atomic_debit',[op,uid,5,'clone']);
    await expectError('SELECT public.lisan_credit_refund($1,$2,$3,$4,$5)',[id(),uid,5,op,'proportional_subscription_up']);
    assert.deepEqual(await balances(),{subscription:0,permanent:10});
  });
  await check('an unstarted clone with lost debit replies is cancelled or refunded safely',async()=>{
    await reset();const op=id();await call('lisan_atomic_debit',[op,uid,5,'clone']);
    const cancelled=await call('lisan_cancel_clone_debit',[op,uid,5]);
    assert.equal(cancelled.work_status,'failed');assert.equal(cancelled.refund_due,5);
    await call('lisan_credit_refund',[id(),uid,5,op,'proportional_subscription_up']);
    assert.deepEqual(await balances(),{subscription:5,permanent:10});
    const late=id();assert.equal((await call('lisan_cancel_clone_debit',[late,uid,5])).reason,'cancelled');
    assert.equal((await call('lisan_atomic_debit',[late,uid,5,'clone'])).status,'failed');
    assert.deepEqual(await balances(),{subscription:5,permanent:10});
  });
  await check('refund history failure leaves a durable pending refund and no false history',async()=>{
    await reset();const op=id(),refund=id();await call('lisan_atomic_debit',[op,uid,8,'clone']);
    await call('lisan_credit_finish',[op,'failed',8]);
    await db.exec(`CREATE FUNCTION public.test_refund_fault() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF NEW.credits < 0 THEN RAISE EXCEPTION 'synthetic refund history failure'; END IF; RETURN NEW; END $$;
      CREATE TRIGGER test_refund_fault BEFORE INSERT ON public.credit_spends FOR EACH ROW EXECUTE FUNCTION public.test_refund_fault();`);
    try {await expectError('SELECT public.lisan_credit_refund($1,$2,$3,$4,$5)',[refund,uid,8,op,'proportional_subscription_up']);}
    finally {await db.exec('DROP TRIGGER test_refund_fault ON public.credit_spends; DROP FUNCTION public.test_refund_fault();');}
    assert.deepEqual(await balances(),{subscription:0,permanent:7});
    assert.equal((await call('lisan_pending_clone_refunds',[])).length,1);
    assert.equal((await call('lisan_credit_refund',[refund,uid,8,op,'proportional_subscription_up'])).status,'done');
    assert.equal((await call('lisan_pending_clone_refunds',[])).length,0);
    assert.deepEqual(await balances(),{subscription:5,permanent:10});
  });
  await check('null bucket is treated as zero, overflow grant leaves receipt recoverable',async()=>{
    await reset();await db.query('UPDATE public.profiles SET subscription_credits=NULL,credits=2147483647 WHERE id=$1',[uid]);
    await expectError('SELECT public.lisan_fulfill_pack($1,$2,$3)',[uid,'overflow',100]);
    assert.equal((await one("SELECT count(*)::int AS n FROM public.lisan_credit_pack_receipts WHERE receipt='overflow'")).n,0);
    assert.equal((await call('lisan_atomic_debit',[id(),uid,5])).taken_subscription,0);
  });
  await check('job ID supports the existing UUID history column and duration is recorded',async()=>{
    await reset();const job=id();await call('lisan_atomic_debit',[id(),uid,1,'generate',job,12.5]);
    const row=await one('SELECT job_id,generated_seconds::float AS seconds FROM public.credit_spends');
    assert.equal(row.job_id,job);assert.equal(row.seconds,12.5);
  });
  await check('missing existing duration column aborts install instead of enabling billing',async()=>{
    await db.exec('ALTER TABLE public.credit_spends DROP COLUMN generated_seconds');
    try {await assert.rejects(db.exec(debit));} finally {await db.exec('ROLLBACK');}
    await db.exec('ALTER TABLE public.credit_spends ADD COLUMN generated_seconds numeric');
  });
  await check('rollback drops only new functions and retains audit tables and old functions',async()=>{
    await db.exec(`BEGIN;
      DROP FUNCTION public.lisan_billing_ready();
      DROP FUNCTION public.lisan_atomic_debit(uuid,uuid,integer,text,text,double precision);
      DROP FUNCTION public.lisan_fulfill_pack(uuid,text,integer);
      DROP FUNCTION public.lisan_pending_clone_refunds();
      DROP FUNCTION public.lisan_cancel_clone_debit(uuid,uuid,integer);
      DROP FUNCTION public.lisan_credit_refund(uuid,uuid,integer,uuid,text);
      DROP FUNCTION public.lisan_credit_finish(uuid,text,integer);
      DROP FUNCTION public.lisan_credit_begin(uuid,uuid,text,integer,uuid);
      COMMIT;`);
    assert.deepEqual(await oldBodies(),before);
    assert.ok((await one("SELECT to_regclass('public.lisan_credit_operations') AS table_name")).table_name);
  });
  console.log(`${passed} SQL integration groups passed. In-memory only; not a multi-session concurrency test.`);
} finally {await db.close();}

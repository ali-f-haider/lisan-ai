// Actual SQL in a local in-memory PostgreSQL runtime; no hosted database.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const {PGlite}=await import(pathToFileURL(path.resolve(process.argv[2])).href);
const db=new PGlite();let passed=0,sequence=0;
const uid='00000000-0000-4000-8000-000000000001';
const id=()=>`00000000-0000-4000-9000-${String(++sequence).padStart(12,'0')}`;
const one=async(sql,args=[]) => (await db.query(sql,args)).rows[0];
const call=async(name,args=[]) => (await one(`SELECT public.${name}(${args.map((_,i)=>'$'+(i+1)).join(',')}) AS result`,args)).result;
async function check(name,run){await run();passed++;console.log('PASS '+name);}
try{
  console.log('Local runtime: '+(await one('SELECT version() AS version')).version);
  await db.exec(`CREATE ROLE anon;CREATE ROLE authenticated;CREATE ROLE service_role;
    CREATE TABLE public.profiles(id uuid PRIMARY KEY,display_name text,credits integer,subscription_credits integer);
    CREATE TABLE public.credit_orders(session_id text PRIMARY KEY,uid uuid,credits integer);
    CREATE TABLE public.credit_spends(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,uid uuid,
      action text,credits integer,job_id uuid,generated_seconds numeric,created_at timestamptz DEFAULT now());`);
  await db.query('INSERT INTO public.profiles VALUES($1,$2,100,100)',[uid,'private customer']);
  for(const file of ['2026-10-credit-operations.sql','2026-10-atomic-credit-debit.sql'])
    await db.exec(await fs.readFile(path.join(root,'sql',file),'utf8'));
  const bodies=()=>db.query("SELECT proname,md5(prosrc) AS body FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND proname LIKE 'lisan_%' AND proname<>'lisan_billing_health' ORDER BY proname");
  const before=(await bodies()).rows;
  const health=await fs.readFile(path.join(root,'sql/2026-10-billing-health.sql'),'utf8');
  await check('health installation only adds a new function and preserves all existing definitions',async()=>{
    await db.exec(health);assert.deepEqual((await bodies()).rows,before);
  });
  await check('browser roles cannot execute the read-only health function; server can',async()=>{
    const grants=await one(`SELECT has_function_privilege('anon','public.lisan_billing_health()','EXECUTE') AS anon,
      has_function_privilege('authenticated','public.lisan_billing_health()','EXECUTE') AS member,
      has_function_privilege('service_role','public.lisan_billing_health()','EXECUTE') AS server`);
    assert.deepEqual(grants,{anon:false,member:false,server:true});
    await db.exec('SET ROLE service_role');try{assert.equal((await call('lisan_billing_health')).ready.version,1);}finally{await db.exec('RESET ROLE');}
  });
  await check('empty health lists have zero counts and readiness flags',async()=>{
    const result=await call('lisan_billing_health');assert.equal(result.ready.debit,true);
    for(const value of Object.values(result.lists))assert.deepEqual(value,{count:0,rows:[]});
  });
  await check('pending counts include all stale requests but return only the newest twenty',async()=>{
    for(let n=0;n<25;n++){
      const op=id();await call('lisan_credit_begin',[op,uid,'debit',1,null]);
      await db.query("UPDATE public.lisan_credit_operations SET created_at=now()-($2::int * interval '1 minute') WHERE operation_id=$1",[op,20+n]);
    }
    await call('lisan_credit_begin',[id(),uid,'debit',1,null]);
    const result=(await call('lisan_billing_health')).lists.pending;
    assert.equal(result.count,25);assert.equal(result.rows.length,20);
    assert.ok(result.rows.every(row=>row.age_seconds>=1200));
    assert.ok(result.rows[0].age_seconds < result.rows[19].age_seconds);
  });
  await check('only the remaining confirmed refund is owed and cloning is its own list',async()=>{
    const clone=id(),regen=id();await call('lisan_atomic_debit',[clone,uid,8,'clone',null,null]);
    await call('lisan_credit_finish',[clone,'failed',8]);
    await call('lisan_credit_refund',[id(),uid,5,clone,'proportional_subscription_up']);
    await call('lisan_atomic_debit',[regen,uid,4,'regenerate',null,null]);
    await call('lisan_credit_finish',[regen,'failed',4]);
    const result=(await call('lisan_billing_health')).lists;
    assert.equal(result.refunds_owed.count,2);assert.equal(result.clone_refunds.count,1);
    assert.equal(result.clone_refunds.rows[0].amount,3);
    const worker=await call('lisan_pending_clone_refunds');assert.equal(worker[0].operation_id,clone);
  });
  await check('legacy_review is detected from the actual pending receipt and no payment IDs leak',async()=>{
    await db.query('INSERT INTO public.credit_orders VALUES($1,$2,30)',['private-checkout-receipt',uid]);
    assert.equal((await call('lisan_fulfill_pack',[uid,'private-checkout-receipt',30])).status,'legacy_review');
    const result=await call('lisan_billing_health');assert.equal(result.lists.legacy_packs.count,1);
    assert.match(result.lists.legacy_packs.rows[0].operation_id,/^[0-9a-f-]{36}$/);
    assert.ok(!JSON.stringify(result).includes('private-checkout-receipt'));
    assert.ok(!JSON.stringify(result).includes('private customer'));
  });
  await check('reading diagnostics changes no balance, operation or receipt',async()=>{
    const snapshot=async()=>JSON.stringify((await db.query(`SELECT jsonb_build_object(
      'profiles',(SELECT jsonb_agg(to_jsonb(p)) FROM public.profiles p),
      'operations',(SELECT jsonb_agg(to_jsonb(o)) FROM public.lisan_credit_operations o),
      'packs',(SELECT jsonb_agg(to_jsonb(p)) FROM public.lisan_credit_pack_receipts p),
      'history',(SELECT jsonb_agg(to_jsonb(h)) FROM public.credit_spends h)) AS result`)).rows);
    const before=await snapshot();await call('lisan_billing_health');assert.equal(await snapshot(),before);
  });
  await check('the supplied signup trigger executes once and currently grants a fixed 100',async()=>{
    await db.exec('CREATE SCHEMA auth;CREATE TABLE auth.users(id uuid PRIMARY KEY,email text,raw_user_meta_data jsonb);');
    await db.exec(await fs.readFile(path.join(root,'tests/fixtures/signup_credit_trigger.sql'),'utf8'));
    const account=id();await db.query('INSERT INTO auth.users VALUES($1,$2,$3)',[account,'synthetic@example.test',{}]);
    assert.equal((await one('SELECT credits FROM public.profiles WHERE id=$1',[account])).credits,100);
    await assert.rejects(db.query('INSERT INTO auth.users VALUES($1,$2,$3)',[account,'synthetic@example.test',{}]));
    assert.equal((await one('SELECT credits FROM public.profiles WHERE id=$1',[account])).credits,100);
  });
  await check('undo removes only health and leaves old billing available',async()=>{
    await db.exec('DROP FUNCTION public.lisan_billing_health();');
    assert.deepEqual((await bodies()).rows,before);assert.equal((await call('lisan_billing_ready')).debit,true);
  });
  console.log(passed+' SQL integration groups passed. In-memory only; no hosted schema or multi-session verification.');
}finally{await db.close();}

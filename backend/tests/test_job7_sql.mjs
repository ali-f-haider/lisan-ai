// Run the actual new SQL in isolated PostgreSQL, never against a hosted database.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const {PGlite}=await import(pathToFileURL(path.resolve(process.argv[2])).href);
const db=new PGlite();let passed=0,seq=0;
const uid='00000000-0000-4000-8000-000000000001',other='00000000-0000-4000-8000-000000000002';
const id=()=>`00000000-0000-4000-9000-${String(++seq).padStart(12,'0')}`;
const row=async(sql,args=[]) => (await db.query(sql,args)).rows[0];
const call=async(name,args=[]) => (await row(`SELECT public.${name}(${args.map((_,i)=>'$'+(i+1)).join(',')}) AS value`,args)).value;
const check=async(name,body)=>{await body();passed++;console.log('PASS '+name);};
const definition=async()=> (await db.query("SELECT proname,md5(prosrc) AS body FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND proname LIKE 'lisan_%' ORDER BY proname")).rows;
const install=async(name)=>db.exec(await fs.readFile(path.join(root,'sql',name),'utf8'));
try{
  console.log('Local runtime: '+(await row('SELECT version() AS version')).version);
  await db.exec(`CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;
    CREATE TABLE public.profiles(id uuid PRIMARY KEY,display_name text,credits integer DEFAULT 0,
      subscription_credits integer DEFAULT 0,subscription_clones_used integer DEFAULT 0,
      subscription_current_period_end timestamptz);
    CREATE TABLE public.credit_spends(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,uid uuid,action text,
      credits integer,job_id uuid,generated_seconds numeric,created_at timestamptz DEFAULT now());
    CREATE TABLE public.credit_audit(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,admin text,
      target_uid uuid,delta integer,reason text,created_at timestamptz DEFAULT now());
    CREATE TABLE public.credit_orders(session_id text PRIMARY KEY,uid uuid,credits integer);
    CREATE TABLE public.subscription_invoices(invoice_id text PRIMARY KEY,uid uuid,credits integer);
    CREATE TABLE public.pricing_config(id text,free_credits numeric,updated_at timestamptz);
    CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY,email text,raw_user_meta_data jsonb);`);
  await db.exec(await fs.readFile(path.join(root,'tests/fixtures/signup_credit_trigger.sql'),'utf8'));
  await db.query('INSERT INTO public.profiles(id,credits,subscription_credits) VALUES($1,100,37),($2,80,25)',[uid,other]);
  for(const name of ['2026-10-credit-operations.sql','2026-10-atomic-credit-debit.sql','2026-10-billing-health.sql']) await install(name);
  const before=await definition();
  await check('new money SQL preserves every existing function',async()=>{
    await install('2026-10-08-01-admin-adjust.sql');await install('2026-10-08-02-subscription-grant.sql');
    await install('2026-10-08-03-billing-health-v2.sql');
    const after=await definition();for(const fn of before)assert.deepEqual(after.find(x=>x.proname===fn.proname),fn);
  });
  await check('every new money/health function and receipt is server-only',async()=>{
    for(const name of ['lisan_admin_begin(uuid,uuid,integer,text)','lisan_admin_adjust(uuid,uuid,integer,text)',
      'lisan_subscription_begin(text,uuid,integer,timestamptz,timestamptz)',
      'lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz)','lisan_subscription_ready()',
      'lisan_billing_health_v2()']){
      assert.deepEqual(await row(`SELECT has_function_privilege('anon',$1,'EXECUTE') AS anon,
        has_function_privilege('authenticated',$1,'EXECUTE') AS member,
        has_function_privilege('service_role',$1,'EXECUTE') AS server`,['public.'+name]),{anon:false,member:false,server:true});
    }
    for(const table of ['lisan_admin_adjustments','lisan_subscription_receipts','lisan_billing_review_events'])
      assert.deepEqual(await row(`SELECT has_table_privilege('anon',$1,'SELECT') AS anon,
        has_table_privilege('authenticated',$1,'SELECT') AS member`,['public.'+table]),{anon:false,member:false});
  });
  await check('admin delta applies after a different atomic spend without overwriting it',async()=>{
    await call('lisan_atomic_debit',[id(),uid,87,'synthetic',null,null]); // 37 subscription + 50 permanent.
    const result=await call('lisan_admin_adjust',[id(),uid,50,'Synthetic reason']);
    assert.equal(result.balance_before,50);assert.equal(result.balance_after,100);assert.equal(result.actual_delta,50);
    assert.equal((await row('SELECT subscription_credits FROM public.profiles WHERE id=$1',[uid])).subscription_credits,0);
  });
  await check('lost admin reply replays once; new intent changes again; mismatch is refused',async()=>{
    const op=id(),args=[op,uid,23,'Synthetic reason'];
    const first=await call('lisan_admin_adjust',args);const again=await call('lisan_admin_adjust',args);
    assert.equal(first.balance_after,123);assert.equal(again.balance_after,123);assert.equal(again.replayed,true);
    assert.equal((await call('lisan_admin_adjust',[op,other,23,'Synthetic reason'])).status,'mismatch');
    assert.equal((await call('lisan_admin_adjust',[op,uid,24,'Synthetic reason'])).status,'mismatch');
    assert.equal((await call('lisan_admin_adjust',[id(),uid,23,'Synthetic reason'])).balance_after,146);
  });
  await check('oversized deduction clamps to zero, writes actual history and one audit reason',async()=>{
    const op=id(),result=await call('lisan_admin_adjust',[op,other,-100,'Synthetic reason']);
    assert.equal(result.actual_delta,-80);assert.equal(result.balance_after,0);
    assert.equal((await row("SELECT credits FROM public.credit_spends WHERE uid=$1 AND action='admin_adjustment' ORDER BY id DESC LIMIT 1",[other])).credits,80);
    assert.equal((await row('SELECT delta FROM public.credit_audit WHERE target_uid=$1 ORDER BY id DESC LIMIT 1',[other])).delta,-80);
  });
  await check('history or audit failure rolls back admin money and keeps an unconfirmed receipt',async()=>{
    for(const table of ['credit_spends','credit_audit']){
      const op=id();await call('lisan_admin_begin',[op,uid,17,'Synthetic reason']);
      const balance=(await row('SELECT credits FROM public.profiles WHERE id=$1',[uid])).credits;
      await db.exec(`CREATE FUNCTION public.synthetic_history_error() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END $$;
        CREATE TRIGGER synthetic_failure BEFORE INSERT ON public.${table} FOR EACH ROW EXECUTE FUNCTION public.synthetic_history_error();`);
      await assert.rejects(call('lisan_admin_adjust',[op,uid,17,'Synthetic reason']));
      assert.equal((await row('SELECT credits FROM public.profiles WHERE id=$1',[uid])).credits,balance);
      assert.equal((await row('SELECT status FROM public.lisan_admin_adjustments WHERE operation_id=$1',[op])).status,'pending');
      await db.exec(`DROP TRIGGER synthetic_failure ON public.${table};DROP FUNCTION public.synthetic_history_error();`);
      assert.equal((await call('lisan_admin_adjust',[op,uid,17,'Synthetic reason'])).balance_after,balance+17);
    }
  });
  const invoiceArgs=['synthetic-invoice',uid,120,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z'];
  await check('subscription allowance/reset/expiry/grant history commit together and replay without extra grants',async()=>{
    await db.query('UPDATE public.profiles SET subscription_credits=37,subscription_clones_used=9 WHERE id=$1',[uid]);
    const permanent=(await row('SELECT credits FROM public.profiles WHERE id=$1',[uid])).credits;
    assert.equal((await call('lisan_subscription_grant',invoiceArgs)).status,'done');
    assert.equal((await call('lisan_subscription_grant',invoiceArgs)).replayed,true);
    assert.deepEqual(await row('SELECT credits,subscription_credits,subscription_clones_used FROM public.profiles WHERE id=$1',[uid]),
      {credits:permanent,subscription_credits:120,subscription_clones_used:0});
    assert.equal((await row("SELECT count(*)::int AS count FROM public.credit_spends WHERE uid=$1 AND action='subscription_grant'",[uid])).count,1);
    assert.equal((await row("SELECT credits FROM public.credit_spends WHERE uid=$1 AND action='subscription_expiry'",[uid])).credits,37);
  });
  await check('invoice outage leaves no premature fulfilled receipt; recovery delivers exactly once',async()=>{
    const args=['failed-then-recovered',other,55,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z'];
    await call('lisan_subscription_begin',args);
    await db.exec(`CREATE FUNCTION public.synthetic_history_error() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END $$;
      CREATE TRIGGER synthetic_failure BEFORE INSERT ON public.credit_spends FOR EACH ROW EXECUTE FUNCTION public.synthetic_history_error();`);
    await assert.rejects(call('lisan_subscription_grant',args));
    assert.equal((await row('SELECT subscription_credits FROM public.profiles WHERE id=$1',[other])).subscription_credits,25);
    assert.equal((await row('SELECT status FROM public.lisan_subscription_receipts WHERE invoice_id=$1',[args[0]])).status,'pending');
    await db.exec('DROP TRIGGER synthetic_failure ON public.credit_spends;DROP FUNCTION public.synthetic_history_error();');
    assert.equal((await call('lisan_subscription_grant',args)).status,'done');
    assert.equal((await row('SELECT credits,subscription_credits FROM public.profiles WHERE id=$1',[other])).credits,0);
  });
  await check('invoice account, amount and both period bounds are immutable; mismatch is durably visible',async()=>{
    for(const [index,value] of [[1,other],[2,121],[3,'2026-10-02T00:00:00Z'],[4,'2026-11-02T00:00:00Z']]){
      const args=[...invoiceArgs];args[index]=value;assert.equal((await call('lisan_subscription_grant',args)).status,'mismatch');
    }
    const health=await call('lisan_billing_health_v2');assert.ok(health.lists.receipt_mismatches.count>=4);
    assert.equal((await row('SELECT subscription_credits FROM public.profiles WHERE id=$1',[uid])).subscription_credits,120);
    assert.ok(!JSON.stringify(health).includes('synthetic-invoice'));
  });
  await check('older service periods are stale and cannot replace a newer allowance',async()=>{
    const args=['late-old',uid,99,'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z'];
    assert.equal((await call('lisan_subscription_grant',args)).status,'stale');
    assert.equal((await row('SELECT subscription_credits FROM public.profiles WHERE id=$1',[uid])).subscription_credits,120);
    assert.equal((await call('lisan_billing_health_v2')).lists.subscription_stale.count,1);
  });
  await check('historical receipts remain review-only; undated records have unknown age',async()=>{
    await db.query('INSERT INTO public.subscription_invoices VALUES($1,$2,40)',['legacy-private-invoice',uid]);
    assert.equal((await call('lisan_subscription_grant',['legacy-private-invoice',uid,40,'2026-11-01T00:00:00Z','2026-12-01T00:00:00Z'])).status,'legacy_review');
    const result=(await call('lisan_billing_health_v2')).lists.subscription_legacy;
    assert.equal(result.count,1);assert.equal(result.rows[0].age_seconds,null);
    assert.ok(!JSON.stringify(result).includes('legacy-private-invoice'));
  });
  await check('extended health keeps full counts/newest twenty and changes no money',async()=>{
    for(let n=0;n<25;n++)await call('lisan_admin_begin',[id(),uid,1,'Synthetic reason']);
    const before=(await row('SELECT credits,subscription_credits FROM public.profiles WHERE id=$1',[uid]));
    const result=await call('lisan_billing_health_v2');assert.equal(result.lists.admin_pending.count,25);
    assert.equal(result.lists.admin_pending.rows.length,20);assert.equal(result.ready.admin_adjust,true);assert.equal(result.ready.subscription_grant,true);
    assert.deepEqual((await row('SELECT credits,subscription_credits FROM public.profiles WHERE id=$1',[uid])),before);
  });
  await check('invalid deltas, dates and missing accounts never change any balance',async()=>{
    for(const delta of [0,10001,-10001])await assert.rejects(call('lisan_admin_adjust',[id(),uid,delta,'Synthetic reason']));
    await assert.rejects(call('lisan_subscription_grant',['bad-date',uid,1,'2026-11-01','2026-10-01']));
    await assert.rejects(call('lisan_admin_adjust',[id(),id(),5,'Synthetic reason']));
  });
  await check('undo disables new actions without removing receipts; reinstall preserves replay protection',async()=>{
    const args=[id(),uid,7,'Synthetic reason'];await call('lisan_admin_adjust',args);
    const balance=(await row('SELECT credits FROM public.profiles WHERE id=$1',[uid])).credits;
    const files=['2026-10-08-03-billing-health-v2.sql','2026-10-08-02-subscription-grant.sql','2026-10-08-01-admin-adjust.sql'];
    for(const name of files){
      const sql=await fs.readFile(path.join(root,'sql',name),'utf8');
      const undo=sql.slice(sql.indexOf('-- UNDO')).split(/\r?\n/).filter(x=>/^-- (?:BEGIN;|DROP FUNCTION|NOTIFY |COMMIT;)/.test(x)).map(x=>x.slice(3)).join('\n');
      await db.exec(undo);
    }
    await assert.rejects(call('lisan_admin_adjust',args));
    await assert.rejects(call('lisan_subscription_grant',invoiceArgs));
    assert.equal((await row('SELECT count(*)::int AS count FROM public.lisan_admin_adjustments')).count>0,true);
    for(const name of [...files].reverse())await install(name);
    assert.equal((await call('lisan_admin_adjust',args)).replayed,true);
    assert.equal((await call('lisan_subscription_grant',invoiceArgs)).replayed,true);
    assert.equal((await row('SELECT credits FROM public.profiles WHERE id=$1',[uid])).credits,balance);
  });
  const oldSignup=await row("SELECT prosrc,proconfig FROM pg_proc WHERE oid='public.handle_new_user()'::regprocedure");
  const signupValue=async()=> (await row('SELECT public.lisan_signup_credit_amount() AS amount')).amount;
  const signupSetting=async(value)=>{await db.exec('DELETE FROM public.pricing_config');await db.query("INSERT INTO public.pricing_config VALUES('singleton',$1,now())",[value]);};
  await check('optional signup installs last and its readiness flag accurately reflects the active trigger',async()=>{
    assert.equal((await call('lisan_billing_health_v2')).ready.signup_credits,false);
    await install('2026-10-08-04-signup-credits.sql');
    assert.equal((await call('lisan_billing_health_v2')).ready.signup_credits,true);
    assert.deepEqual(await row("SELECT has_function_privilege('anon','public.lisan_signup_credit_amount()','EXECUTE') AS anon,has_function_privilege('authenticated','public.lisan_signup_credit_amount()','EXECUTE') AS member,has_function_privilege('service_role','public.lisan_signup_credit_amount()','EXECUTE') AS server"),{anon:false,member:false,server:true});
  });
  await check('signup reads the actual admin singleton and grants configured 250 with one history row',async()=>{
    await signupSetting(250);const account=id();
    await db.query('INSERT INTO auth.users VALUES($1,$2,$3)',[account,'synthetic@example.test',{}]);
    assert.equal((await row('SELECT credits FROM public.profiles WHERE id=$1',[account])).credits,250);
    assert.deepEqual(await row('SELECT action,credits FROM public.credit_spends WHERE uid=$1',[account]),{action:'signup_grant',credits:-250});
  });
  await check('missing/invalid/zero/nonfinite/unbounded signup settings use 100; both limits remain valid',async()=>{
    for(const value of [null,'NaN','Infinity','-Infinity',0,-1,1001,1.5,'999999999999999999999999']){
      await signupSetting(value);assert.equal(await signupValue(),100);
    }
    for(const value of [1,100,250,1000]){await signupSetting(value);assert.equal(await signupValue(),value);}
    await db.exec('DELETE FROM public.pricing_config');assert.equal(await signupValue(),100);
    await db.exec('ALTER TABLE public.pricing_config RENAME TO synthetic_saved_pricing');assert.equal(await signupValue(),100);
    await db.exec('ALTER TABLE public.synthetic_saved_pricing RENAME TO pricing_config');
  });
  await check('signup selects the same newest singleton row as the admin reader',async()=>{
    await db.exec("DELETE FROM public.pricing_config;INSERT INTO public.pricing_config VALUES('singleton',250,'2026-10-01'),('singleton',300,'2026-10-02'),('other',900,'2026-10-03')");
    assert.equal(await signupValue(),300);
  });
  await check('an existing profile never receives another signup grant or history row',async()=>{
    const account=id();await db.query('INSERT INTO public.profiles(id,credits) VALUES($1,73)',[account]);
    await db.query('INSERT INTO auth.users VALUES($1,$2,$3)',[account,'synthetic@example.test',{}]);
    assert.equal((await row('SELECT credits FROM public.profiles WHERE id=$1',[account])).credits,73);
    assert.equal((await row('SELECT count(*)::int AS count FROM public.credit_spends WHERE uid=$1',[account])).count,0);
  });
  await check('signup history failure rolls back both profile and auth creation',async()=>{
    await db.exec("CREATE FUNCTION public.synthetic_history_error() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END $$;CREATE TRIGGER synthetic_failure BEFORE INSERT ON public.credit_spends FOR EACH ROW EXECUTE FUNCTION public.synthetic_history_error();");
    const account=id();await assert.rejects(db.query('INSERT INTO auth.users VALUES($1,$2,$3)',[account,'synthetic@example.test',{}]));
    assert.equal((await row('SELECT count(*)::int AS count FROM public.profiles WHERE id=$1',[account])).count,0);
    assert.equal((await row('SELECT count(*)::int AS count FROM auth.users WHERE id=$1',[account])).count,0);
    await db.exec('DROP TRIGGER synthetic_failure ON public.credit_spends;DROP FUNCTION public.synthetic_history_error();');
  });
  await check('optional signup undo restores the exact original function and retains all granted history',async()=>{
    const count=(await row('SELECT count(*)::int AS count FROM public.credit_spends')).count;
    const sql=await fs.readFile(path.join(root,'sql/2026-10-08-04-signup-credits.sql'),'utf8');
    const tail=sql.slice(sql.indexOf('-- UNDO:'));
    const start=tail.indexOf('-- BEGIN;'),end=tail.indexOf('-- COMMIT;')+'-- COMMIT;'.length;
    await db.exec(tail.slice(start,end).split(/\r?\n/).map(line=>line.startsWith('-- ')?line.slice(3):line).join('\n'));
    assert.deepEqual(await row("SELECT prosrc,proconfig FROM pg_proc WHERE oid='public.handle_new_user()'::regprocedure"),oldSignup);
    assert.equal((await row('SELECT count(*)::int AS count FROM public.credit_spends')).count,count);
    assert.equal((await call('lisan_billing_health_v2')).ready.signup_credits,false);
    const account=id();await db.query('INSERT INTO auth.users VALUES($1,$2,$3)',[account,'synthetic@example.test',{}]);
    assert.equal((await row('SELECT credits FROM public.profiles WHERE id=$1',[account])).credits,100);
    assert.equal((await row('SELECT count(*)::int AS count FROM public.credit_spends')).count,count);
  });
  console.log(passed+' SQL integration groups passed; in-memory only, no hosted or multi-session concurrency claim.');
}finally{await db.close();}

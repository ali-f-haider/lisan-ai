// Built-in Node only: exercise real page functions with synthetic browser storage.
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const admin=fs.readFileSync(path.join(root,'admin.html'),'utf8');
const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
const account=fs.readFileSync(path.join(root,'account.html'),'utf8');
const excerpt=(source,a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)+a.length));
function storage(){const map=new Map();return {getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k),map};}
function adminContext(saved=storage()){
  const calls=[],messages=[];
  const c=vm.createContext({window:{crypto:require('node:crypto').webcrypto},localStorage:saved,
    toast:(m,e)=>messages.push([m,e]),loadUsers(){},api:async(url,opts)=>{calls.push(JSON.parse(opts.body));return c.reply;},
    reply:{ok:true,data:{ok:true,operation_complete:true,adjusted_credits:50,new_credits:150}},
    usersCache:[{credits:100}],LisanDialog:{prompt:async()=>c.answers.shift()},answers:[]});
  vm.runInContext(excerpt(admin,'function adminAdjustmentUuid()','function slugifyPackKey('),c);
  return {c,calls,messages,saved,body:{uid:'00000000-0000-4000-8000-000000000001',delta:50,reason:'Synthetic reason',intent:'delta'}};
}
test('admin stores intent before sending; lost reply and reload reuse UUID',async()=>{
  const first=adminContext();first.c.api=async(url,opts)=>{const body=JSON.parse(opts.body);
    first.calls.push(body);assert.equal(JSON.parse(first.saved.getItem('lisan_admin_adjust_v1:'+body.uid)).operation_id,body.operation_id);
    throw new Error('Synthetic lost reply');};
  await first.c.submitAdminAdjustment(first.body);
  const second=adminContext(first.saved);await second.c.submitAdminAdjustment(second.body);
  assert.equal(first.calls[0].operation_id,second.calls[0].operation_id);
  assert.equal(second.saved.map.size,0);assert.match(second.messages[0][0],/50.*150/);
});
test('another deliberate admin action gets a new UUID after confirmation',async()=>{
  const {c,calls,body}=adminContext();await c.submitAdminAdjustment(body);await c.submitAdminAdjustment(body);
  assert.notEqual(calls[0].operation_id,calls[1].operation_id);
});
test('admin simultaneous click and changed unresolved request make no second API call',async()=>{
  const t=adminContext();let resolve;
  t.c.api=async(u,o)=>{t.calls.push(JSON.parse(o.body));return new Promise(r=>resolve=r);};
  const pending=t.c.submitAdminAdjustment(t.body);await t.c.submitAdminAdjustment(t.body);assert.equal(t.calls.length,1);
  resolve({ok:false,data:{error:'Synthetic pending'}});await pending;
  await t.c.submitAdminAdjustment({...t.body,delta:51});assert.equal(t.calls.length,1);assert.equal(t.saved.map.size,1);
});
test('admin storage failure starts no paid adjustment',async()=>{
  const t=adminContext();t.saved.setItem=()=>{throw new Error('Synthetic storage outage');};
  await t.c.submitAdminAdjustment(t.body);assert.equal(t.calls.length,0);
});
test('invalid adjustment or long reason is rejected before saving an intent',async()=>{
  for(const update of [{delta:10001},{delta:0},{delta:1.5},{reason:'x'.repeat(2001)}]){
    const t=adminContext();await t.c.submitAdminAdjustment({...t.body,...update});
    assert.equal(t.calls.length,0);assert.equal(t.saved.map.size,0);
  }
});
test('Set retry after reload retains original delta even when fetched balance has changed',async()=>{
  const t=adminContext();t.saved.setItem('lisan_admin_adjust_v1:'+t.body.uid,JSON.stringify({...t.body,intent:'set',target_amount:150,operation_id:'00000000-0000-4000-8000-000000000002'}));
  t.c.usersCache[0].credits=150;t.c.answers=['150','Synthetic reason'];
  await t.c.setCredits(t.body.uid,0);assert.equal(t.calls[0].delta,50);assert.equal(t.calls[0].operation_id,'00000000-0000-4000-8000-000000000002');
});
function lipContext(saved=storage()){
  const calls=[],elements=new Map();
  const c=vm.createContext({window:{crypto:require('node:crypto').webcrypto},localStorage:saved,
    Uint8Array,currentJobId:'a'.repeat(32),subsText:en=>en,
    shortRegenerationUuid:()=>require('node:crypto').randomUUID(),notify(){},safePercent:x=>x,
    document:{getElementById:id=>{if(!elements.has(id))elements.set(id,{disabled:false,style:{},classList:{add(){},remove(){}},innerHTML:''});return elements.get(id);}},
    selectedLipsyncRes:()=>c.resolution,resolution:'720p',lipsyncPollTimer:null,clearInterval(){},setInterval(){return 1;},
    openBuyModal(){},encodeURIComponent,Date,fetchUsage(){},refreshCredits(){},
    fetch:async(url,opts)=>{calls.push(opts?JSON.parse(opts.body):url);return {ok:true,json:async()=>c.reply};},
    reply:{status:'started',credits_charged:10},updateBadges(){}});
  vm.runInContext(excerpt(app,'function lipsyncIntentKey()','async function checkLipsyncProgress()'),c);
  return {c,saved,calls,elements,payload:{job_id:'a'.repeat(32),resolution:'720p'}};
}
test('lip-sync saves before request and reload restores the exact intent',async()=>{
  const first=lipContext();first.c.fetch=async(u,o)=>{const body=JSON.parse(o.body);first.calls.push(body);
    assert.equal(JSON.parse(first.saved.getItem('lisan_lipsync_v1:'+body.job_id)).operation_id,body.operation_id);throw new Error('lost');};
  await first.c.runLipsync();const second=lipContext(first.saved);await second.c.runLipsync();
  assert.equal(first.calls[0].operation_id,second.calls[0].operation_id);assert.equal(second.saved.map.size,1);
});
test('lip-sync prevents repeated clicks in flight and refuses changed unresolved settings',async()=>{
  const t=lipContext();let resolve;
  t.c.fetch=async(u,o)=>{t.calls.push(JSON.parse(o.body));return new Promise(r=>resolve=r);};
  const pending=t.c.runLipsync();await t.c.runLipsync();assert.equal(t.calls.length,1);
  resolve({ok:false,json:async()=>({error:'Synthetic pending'})});await pending;
  t.c.resolution='1080p';await t.c.runLipsync();assert.equal(t.calls.length,1);
});
test('lip-sync completion clears only its matching ID; a new take receives new ID',()=>{
  const t=lipContext(),first=t.c.lipsyncIntent(t.payload);t.c.saveLipsyncIntent(first);
  t.c.finishLipsyncIntent({operation_complete:false,operation_id:first.operation_id});assert.equal(t.saved.map.size,1);
  t.c.finishLipsyncIntent({operation_complete:true,operation_id:'different'});assert.equal(t.saved.map.size,1);
  t.c.finishLipsyncIntent({operation_complete:true,operation_id:first.operation_id});assert.equal(t.saved.map.size,0);
  assert.notEqual(t.c.lipsyncIntent(t.payload).operation_id,first.operation_id);
});
test('lip-sync storage failure makes no generation request',async()=>{
  const t=lipContext();t.saved.setItem=()=>{throw new Error('storage failed');};await t.c.runLipsync();assert.equal(t.calls.length,0);
});
test('allowance and signup are grants, expiry is spending; labels exist in both languages',()=>{
  const c=vm.createContext({LANG:'en'});
  vm.runInContext(excerpt(account,'function creditChange(','\nfunction ',account),c);
  assert.equal(c.creditChange(-120,'subscription_grant').text,'+120');
  assert.equal(c.creditChange(-250,'signup_grant').text,'+250');
  assert.equal(c.creditChange(37,'subscription_expiry').text,'−37');
  for(const action of ['subscription_grant','subscription_expiry','signup_grant'])assert.equal((account.match(new RegExp(action+':','g'))||[]).length,2);
});

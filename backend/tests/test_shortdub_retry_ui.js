// Built-in Node tests, no website, provider or database calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;
const app = fs.readFileSync(path.join(__dirname,'..','app.js'),'utf8');
const admin = fs.readFileSync(path.join(__dirname,'..','admin.html'),'utf8');
function between(source,first,last){const start=source.lastIndexOf(first);assert.ok(start>=0);return source.slice(start,source.indexOf(last,start));}
function storage(){const data=new Map();return {data,getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};}
function context(saved=storage()) {
  const calls=[], messages=[];
  const c=vm.createContext({window:{crypto,currentLang:'en'},localStorage:saved,
    document:{getElementById:()=>({value:'exact'}),querySelector:()=>null},
    segmentsData:[{segment_id:'seg_0',arabic_text:'مرحبا',speaker:'Speaker 1',start:0,waqf:'auto'}],
    speakerVoices:{'Speaker 1':'voice'},currentJobId:'a'.repeat(32),totalDuration:4,
    shortWaqfMode:()=> 'auto', subsText:en=>en, notify:(type,message)=>messages.push({type,message}),
    refreshCredits(){},fetchUsage(){},setBadge(){},
    async fetch(url,options){
      const payload=JSON.parse(options.body);calls.push({url,payload});
      if(url.endsWith('/quote')){
        if(c.quoteWait)await c.quoteWait;
        return {ok:true,json:async()=>({credits:3})};
      }
      if(c.lose){c.lose=false;throw new Error('Synthetic lost response');}
      return {ok:true,json:async()=>({status:'success',credits_charged:3,operation_complete:true})};
    }
  });
  vm.runInContext(between(app,'function shortRegenerationError(','// Pure editing action'),c);
  return {c,calls,messages,saved};
}
const button=()=>({disabled:false,textContent:''});
test('a lost reply retries the same ID and accepted price, then a new click gets a fresh ID',async()=>{
  const {c,calls,saved}=context();c.lose=true;
  await c.regenerateLine(0,button());assert.equal(saved.data.size,1);
  await c.regenerateLine(0,button());assert.equal(saved.data.size,0);
  assert.deepEqual(calls.map(x=>x.url),['/api/regenerate_line/quote','/api/regenerate_line','/api/regenerate_line']);
  assert.equal(calls[1].payload.operation_id,calls[2].payload.operation_id);
  assert.equal(calls[2].payload.accepted_credits,3);
  await c.regenerateLine(0,button());assert.notEqual(calls[4].payload.operation_id,calls[2].payload.operation_id);
});
test('reload restores the unresolved click from browser storage',async()=>{
  const first=context();first.c.lose=true;await first.c.regenerateLine(0,button());
  const second=context(first.saved);await second.c.regenerateLine(0,button());
  assert.equal(second.calls.length,1);assert.equal(second.calls[0].payload.operation_id,first.calls[1].payload.operation_id);
});
test('a reloaded project with reordered object keys still restores the same request',async()=>{
  const first=context();first.c.lose=true;await first.c.regenerateLine(0,button());
  const second=context(first.saved);
  second.c.segmentsData=[Object.fromEntries(Object.entries(second.c.segmentsData[0]).reverse())];
  await second.c.regenerateLine(0,button());
  assert.equal(second.calls.length,1);assert.equal(second.calls[0].payload.operation_id,first.calls[1].payload.operation_id);
});
test('editing unresolved work does not discard its receipt or start another paid request',async()=>{
  const {c,calls,saved,messages}=context();c.lose=true;await c.regenerateLine(0,button());
  const before=[...saved.data.values()][0];c.segmentsData[0].arabic_text='نص آخر';
  await c.regenerateLine(0,button());
  assert.equal(calls.length,2);assert.equal([...saved.data.values()][0],before);
  assert.ok(messages.some(x=>x.message.includes('unresolved')));
});
test('storage failure prevents the paid request',async()=>{
  const saved=storage();saved.setItem=()=>{throw new Error('Synthetic blocked storage');};
  const {c,calls}=context(saved);await c.regenerateLine(0,button());
  assert.deepEqual(calls.map(x=>x.url),['/api/regenerate_line/quote']);
});
test('two controls for one line share the in-flight browser lock',async()=>{
  const {c,calls}=context();let release;
  c.quoteWait=new Promise(resolve=>{release=resolve;});
  const first=c.regenerateLine(0,button());await c.regenerateLine(0,button());release();await first;
  assert.equal(calls.filter(x=>x.url==='/api/regenerate_line').length,1);
});
test('a confirmed failed-and-refunded request ends its browser receipt',async()=>{
  const {c,saved}=context();
  c.fetch=async(url)=>({ok:true,json:async()=>url.endsWith('/quote')?{credits:3}:{status:'error',error:'Refunded',operation_complete:true,credits_refunded:3}});
  await c.regenerateLine(0,button());assert.equal(saved.data.size,0);
});
test('Arabic refund notices keep the completed refund distinct from a pending one',()=>{
  const {c}=context();c.window.currentLang='ar';
  assert.equal(c.shortRegenerationError('The line could not be re-spoken. Your credits were refunded.'),'تعذرت إعادة نطق السطر. أُعيدت أرصدتك.');
  assert.ok(c.shortRegenerationError('The line could not be re-spoken. Your refund is pending and will be retried automatically.').includes('قيد الانتظار'));
});
function healthContext(data){
  const element=()=>({textContent:'',children:[],appendChild(child){this.children.push(child);},replaceChildren(){this.children=[];}});
  const status=element(),host=element(),calls=[];
  const c=vm.createContext({document:{getElementById:id=>id==='billingHealthStatus'?status:host,createElement:element},
    api:async(url)=>{calls.push(url);return {ok:true,data};}});
  vm.runInContext(between(admin,'async function loadBillingHealth()','async function loadMaintenance()'),c);
  return {c,status,host,calls};
}
test('Billing health shows the quiet state with one read-only request',async()=>{
  const lists=Object.fromEntries(['pending','refunds_owed','legacy_packs','clone_refunds'].map(key=>[key,{count:0,rows:[]}])) ;
  const {c,status,calls}=healthContext({installed:true,ready:true,lists});await c.loadBillingHealth();
  assert.equal(status.textContent,'Billing is ready. Nothing needs attention.');
  assert.deepEqual(calls,['/api/admin/billing_health']);
});
test('Billing health handles a missing installation without showing an error',async()=>{
  const {c,status,host}=healthContext({installed:false});await c.loadBillingHealth();
  assert.equal(status.textContent,'Not installed yet');assert.equal(host.children.length,0);
});

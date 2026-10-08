// Execute real page functions with synthetic replies; no browser or network.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const source=Object.fromEntries(['admin.html','account.html','app.js'].map(n=>[n,fs.readFileSync(path.join(root,n),'utf8')]));
function fn(file,name){
  const s=source[file],m=new RegExp('(?:async )?function '+name+'\\(').exec(s);
  assert(m,'Missing '+name);const end=s.indexOf('\n}',m.index);assert(end>m.index);
  return s.slice(m.index,end+2);
}
function element(){return {value:'',checked:false,disabled:false,style:{},children:[],textContent:'',
  removed:false,remove(){this.removed=true;},appendChild(x){this.children.push(x);},replaceChildren(){this.children=[];}};}
function dom(){const elements=new Map();return {elements,getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},
  createElement:element,body:{appendChild(){}},querySelectorAll(){return [];}};}
function reply(body,ok=true){return {ok,status:ok?200:503,json:async()=>body,text:async()=>JSON.stringify(body)};}
function context(){const document=dom(),messages=[],timers=[];const c=vm.createContext({document,window:{currentLang:'en'},
  console:{log(){},error(){}},ADMIN_TOKEN:'synthetic',sessionStorage:{removeItem(){}},location:{href:'unchanged',reload(){}},
  toast:(m,e)=>messages.push([m,!!e]),notify:(kind,m)=>messages.push([m,kind==='error']),
  setTimeout:(f,t)=>{timers.push([f,t]);return 1;},clearTimeout(){},
  LisanDialog:{confirm:async()=>true,alert:(m,o)=>messages.push([m,o.type==='error'])},
  T:{genericFail:'Please try again.',subRenewed:()=> 'Renewed',nothingYet:()=> 'No files'},subsText:en=>en,
  refreshCredits(){},loadMaintenance(){},loadPricing(){},loadHealth(){},loadMyVoices(){},loadUserInfo(){},
  refreshSubscriptionThenLoad:async()=>{},closeBuyModal(){},fmtDay:x=>x,
  fetch:async()=>reply(c.body),body:{ok:true}});
  c.window.location=c.location;return {c,document,messages,timers};
}
function install(t,file,names){for(const n of names)vm.runInContext(fn(file,n),t.c);}
async function pricingPayload(body={ok:true}){
  const t=context();t.c.body=body;t.c.pricingLoaded=true;t.c.slugifyPackKey=s=>s.toLowerCase();
  const values={freeCredits:'250',minReserve:'150',maxVideoMin:'60',transcribeCredits:'3',mergeCredits:'1',charsPerCredit:'60',
    cloneCredits:'5',inworldCharsPerCredit:'65',inworldCloneCredits:'6',inworldSlotLimit:'100',longDubMaxMin:'60',
    longDubFlatCredits:'10',longDubLipsyncMaxMin:'3',longDubAnalysisPerMin:'2.25',lipsyncCreditsPerSec:'40',
    concMax:'2',concNeed:'5.5',concPct:'80',concLimit:'0',asstBudget:'2.5',asstUser:'60',asstCpc:'1.25',
    asstNotes:'Useful notes',geminiCreditsPerCent:'1.5',gaMeasurementId:'G-TEST1234',subscriptionName:'Pro Monthly',
    subscriptionCredits:'4000',subscriptionPriceUsd:'29.95',voiceEngine:'inworld',uiStyle:'new'};
  for(const [id,value] of Object.entries(values))t.document.getElementById(id).value=value;
  for(const id of ['asstEnabled','siteGateEnabled','elevenAlertsEnabled','railwayAlertsEnabled','diskAlertsEnabled'])t.document.getElementById(id).checked=true;
  const row=values=>({querySelectorAll:()=>values.map(value=>({value}))});
  t.document.querySelectorAll=q=>q==='#packsTable tr'?[row(['starter','Starter','100','1.95','0',''])]:
    q==='#plansTable tr'?[row(['studio','Studio','29.95','4000','20','','']),row(['plus','Plus','39.95','6000','30','5','25.5'])]:[];
  t.c.api=async(url,opts)=>{t.payload=JSON.parse(opts.body);return {ok:!body.error&&body.ok!==false,data:body};};
  install(t,'admin.html',['savePricing']);await t.c.savePricing();return t;
}
if(process.argv.includes('--pricing-payload')){
  pricingPayload().then(t=>process.stdout.write(JSON.stringify(t.payload))).catch(e=>{console.error(e);process.exitCode=1;});
}else{
  const test=require('node:test');
  test('admin API rejects HTTP 200 refusals, error-only replies, malformed data and non-200 success bodies',async()=>{
    for(const [body,http] of [[{ok:false},true],[{error:'Write refused'},true],[{ok:true,error:'Write refused'},true],[null,true],[[],true],[{ok:true},false]]){
      const t=context();install(t,'admin.html',['api']);t.c.fetch=async()=>reply(body,http);
      assert.equal((await t.c.api('/synthetic')).ok,false);
    }
    const t=context();install(t,'admin.html',['api']);t.c.fetch=async()=>({ok:true,status:200,text:async()=>'<html>unreadable</html>'});
    assert.equal((await t.c.api('/synthetic')).ok,false);
    t.c.fetch=async()=>{throw Error('Offline');};assert.equal((await t.c.api('/synthetic')).ok,false);
  });
  test('maintenance save and clear show server refusal in red and never show success',async()=>{
    for(const name of ['saveMaintenance','clearMaintenance'])for(const body of [{ok:false,error:'Disk write refused'},{error:'Disk write refused'},{}]){
      const t=context();t.c.body=body;install(t,'admin.html',['api',name]);await t.c[name]();
      assert.equal(t.messages.length,1);assert.equal(t.messages[0][1],true);
      if(body.error)assert.match(t.messages[0][0],/Disk write refused/);
    }
  });
  test('pricing requires explicit confirmation, not just the absence of ok:false',async()=>{
    for(const body of [{ok:false,error:'Integer rejected'},{error:'Integer rejected'},{}]){
      const t=await pricingPayload(body);assert.equal(t.messages[0][1],true);assert.equal(t.timers.length,0);
    }
    const t=await pricingPayload();assert.equal(t.messages[0][1],false);assert.equal(t.payload.subscriptionPlans[0].storage_gb,null);
  });
  test('a failed pricing reload invalidates old loaded values and reports the reason',async()=>{
    const t=context();t.c.pricingLoaded=true;t.c.body={error:'Settings are unavailable.'};
    install(t,'admin.html',['api','loadPricing']);await t.c.loadPricing();
    assert.equal(t.c.pricingLoaded,false);assert.deepEqual(t.messages,[['Settings are unavailable.',true]]);
  });
  test('failed memory diagnostics still replace stale numbers with the unavailable reason',async()=>{
    const t=context();t.c.body={ok:false,error:'Memory measurement is unavailable.'};t.c.loadResourceUsage=()=>{};
    install(t,'admin.html',['api','loadRailwayMemory']);await t.c.loadRailwayMemory();
    assert.equal(t.document.getElementById('rmUsed').textContent,'—');
    assert.match(t.document.getElementById('rmNote').textContent,/Memory measurement is unavailable/);
  });
  test('backup does not turn a contradictory HTTP 200 reply green',async()=>{
    const t=context();t.c.body={ok:false,status:'done',error:'Backup refused'};install(t,'admin.html',['api','runDbBackupNow']);
    await t.c.runDbBackupNow();assert.equal(t.document.getElementById('dbBackupResult').style.color,'#dc2626');
    assert.match(t.document.getElementById('dbBackupResult').textContent,/Backup refused/);
  });
  test('admin errors last 10 seconds and can be dismissed; success keeps 3.5 seconds',()=>{
    const t=context();install(t,'admin.html',['toast']);const made=[];t.document.body.appendChild=e=>made.push(e);
    t.c.toast('No',true);t.c.toast('Yes',false);assert.deepEqual(t.timers.map(x=>x[1]),[10000,3500]);
    made[0].onclick();assert.equal(made[0].removed,true);
  });
  test('admin sign-in rejects a token returned with a refusal or failed HTTP status',async()=>{
    for(const [body,http] of [[{token:'synthetic',ok:false},true],[{token:'synthetic',error:'Refused'},true],[{token:'synthetic'},false]]){
      const t=context();t.document.getElementById('adminCode').value='synthetic';install(t,'admin.html',['doLogin']);
      t.c.showAdmin=()=>assert.fail('Must not show admin on refusal');t.c.fetch=async()=>reply(body,http);
      t.c.doLogin();await new Promise(setImmediate);assert.equal(t.messages[0][1],true);
    }
  });
  test('account mutations refuse errors and require a positive result shape',async()=>{
    const t=context();install(t,'account.html',['safeMsg','accountResponse']);
    for(const body of [{ok:false},{error:'Please retry later.'},{ok:true,error:'Please retry later.'},{}])
      await assert.rejects(t.c.accountResponse(reply(body)));
    await assert.rejects(t.c.accountResponse(reply({url:'/ok',ok:false}),'url'));
    await assert.rejects(t.c.accountResponse(reply({ok:true},false)));
    assert.equal((await t.c.accountResponse(reply({ok:true}))).ok,true);
    assert.equal((await t.c.accountResponse(reply({url:'/ok'}),'url')).url,'/ok');
    await assert.rejects(t.c.accountResponse(reply({already_canceling:true}),'url'));
    assert.equal((await t.c.accountResponse(reply({already_canceling:true}),'cancel')).already_canceling,true);
    await assert.rejects(t.c.accountResponse(reply({error:'gemini api key rejected'})),/Please try again/);
  });
  test('saved voice refusal hides an earlier Saved marker and shows the safe server reason',async()=>{
    const t=context();t.c.body={ok:false,error:'This voice could not be saved.'};
    install(t,'account.html',['safeMsg','accountResponse','saveVoice']);await t.c.saveVoice('v');await new Promise(setImmediate);
    assert.equal(t.document.getElementById('vsaved-v').style.display,'none');
    assert.deepEqual(t.messages,[['This voice could not be saved.',true]]);
  });
  test('failed file deletion keeps the visible file and storage unchanged',async()=>{
    const t=context();t.c.body={error:'File is still in use.'};install(t,'account.html',['safeMsg','accountResponse','deleteMyJob']);
    t.c.renderStorage=()=>assert.fail('Must not change storage on refusal');t.c.deleteMyJob('j');await new Promise(setImmediate);
    assert.equal(t.document.getElementById('job-j').removed,false);assert.deepEqual(t.messages,[['File is still in use.',true]]);
  });
  test('successful file deletion accepts the real status:success contract',async()=>{
    const t=context();t.c.body={status:'success',removed:1,storage:{used_gb:0}};
    install(t,'account.html',['safeMsg','accountResponse','deleteMyJob']);let rendered=false;
    t.c.renderStorage=()=>{rendered=true;};t.c.deleteMyJob('j');await new Promise(setImmediate);
    assert.equal(t.document.getElementById('job-j').removed,true);assert.equal(rendered,true);assert.equal(t.messages.length,0);
  });
  test('successful saved voice and renewal still show their confirmations',async()=>{
    const t=context();install(t,'account.html',['safeMsg','accountResponse','saveVoice','resumeSubscription']);
    t.c.body={ok:true};t.c.saveVoice('v');await new Promise(setImmediate);
    assert.equal(t.document.getElementById('vsaved-v').style.display,'inline');
    t.c.resumeSubscription();await new Promise(setImmediate);assert.deepEqual(t.messages,[['Renewed',false]]);
  });
  test('refused subscription renewal never shows Renewed',async()=>{
    const t=context();t.c.body={ok:false,error:'Renewal is paused.'};install(t,'account.html',['safeMsg','accountResponse','resumeSubscription']);
    t.c.resumeSubscription();await new Promise(setImmediate);assert.deepEqual(t.messages,[['Renewal is paused.',true]]);
  });
  test('portal, cancellation, account deletion and voice deletion reject false replies before navigation/removal',async()=>{
    for(const name of ['manageSubscription','cancelSubscription','confirmDeleteAccount','deleteVoice']){
      const t=context();t.c.body={ok:false,error:'This change is paused.'};
      install(t,'account.html',['safeMsg','accountResponse',name]);t.c[name]('v');await new Promise(setImmediate);
      assert.equal(t.c.location.href,'unchanged');assert.equal(t.document.getElementById('voice-v').removed,false);
      assert.deepEqual(t.messages,[['This change is paused.',true]]);
    }
  });
  test('background subscription refresh reports a refusal while still loading account details',async()=>{
    const t=context();t.c.body={ok:false};let loaded=false;t.c.loadUserInfo=()=>{loaded=true;};
    install(t,'account.html',['safeMsg','accountResponse','refreshSubscriptionThenLoad']);await t.c.refreshSubscriptionThenLoad();
    assert.equal(loaded,true);assert.equal(t.messages[0][1],true);
  });
  test('app money reply guard refuses false, error, malformed and HTTP failure',async()=>{
    const t=context();install(t,'app.js',['settingsErrorMessage','settingsResponse']);
    for(const body of [{ok:false},{error:'Please try later.'},{ok:true,error:'Please try later.'},null,[]])
      await assert.rejects(t.c.settingsResponse(reply(body)));
    await assert.rejects(t.c.settingsResponse(reply({url:'/ok'},false)));
    await assert.rejects(t.c.settingsResponse(reply({error:'inworld service failed'})),/could not be confirmed/);
  });
  test('checkout and subscription buttons unlock on a refused or unconfirmed checkout',async()=>{
    for(const name of ['buyPack','subscribeMonthly'])for(const body of [{ok:false,url:'/bad'},{error:'Payments paused.'},{}]){
      const t=context();t.c.body=body;install(t,'app.js',['settingsErrorMessage','settingsResponse',name]);const btn=element();
      t.c[name]('studio',btn);await new Promise(setImmediate);assert.equal(t.c.location.href,'unchanged');assert.equal(btn.disabled,false);
      assert.equal(t.messages[0][1],true);
    }
  });
  test('plan change must have ok:true and a known change mode',async()=>{
    for(const body of [{ok:false,mode:'reverted'},{mode:'reverted'},{ok:true,mode:'unknown'}]){
      const t=context();t.c.body=body;install(t,'app.js',['settingsErrorMessage','settingsResponse','changeSubscriptionPlan']);
      const btn=element();t.c.changeSubscriptionPlan({key:'pro',name:'Pro'},'current_keep',{price:29,credits:4000},btn);
      await new Promise(setImmediate);assert.equal(btn.disabled,false);assert.equal(t.messages[0][1],true);
    }
  });
  test('current cleanup handler rejects ok:false and partial cleanup without green success',async()=>{
    for(const body of [{ok:false,deleted:0},{deleted:0,errors:['Cannot verify saved voices']}]){
      const t=context();t.c.body=body;t.c.currentJobId='synthetic';
      install(t,'app.js',['settingsErrorMessage']);const s=source['app.js'],a=s.lastIndexOf('window.cleanOldClones = function () {'),b=s.indexOf('\n    };',a);
      vm.runInContext(s.slice(a,b+7),t.c);t.c.window.cleanOldClones();await new Promise(setImmediate);
      assert.equal(t.messages[0][1],true);
    }
  });
  test('all payment JSON callbacks and account saved mutations use the reply guard',()=>{
    assert.equal((source['app.js'].match(/\.then\(settingsResponse\)/g)||[]).length,12);
    for(const name of ['saveVoice','deleteVoice','deleteMyJob','resumeSubscription','confirmDeleteAccount'])
      assert.match(fn('account.html',name),/accountResponse/);
    assert.doesNotMatch(source['app.js'],/notify\("success", "🎉 (Payment complete|Subscription active)/);
    const callbacks=[...source['app.js'].matchAll(/fetch\("\/api\/billing\/(?:checkout|subscribe|change_plan|sync|fulfill)(?:(?!fetch\(|\.catch\()[\s\S]){0,1500}?\.then\(([^\n]*)/g)];
    assert.equal(callbacks.length,7);
    for(const callback of callbacks)assert.match(callback[1],/^settingsResponse\)/,'Each payment callback must guard its reply');
  });
  test('the active credit-sync callback refuses a fake positive amount accompanying ok:false',async()=>{
    const t=context();t.c.body={ok:false,added_sessions_credits:100};install(t,'app.js',['settingsErrorMessage','settingsResponse']);
    const s=source['app.js'],a=s.lastIndexOf('window.syncCreditsNow = function () {'),b=s.indexOf('\n    };',a);
    vm.runInContext(s.slice(a,b+7),t.c);t.c.window.syncCreditsNow();await new Promise(setImmediate);
    assert.equal(t.messages.length,1);assert.equal(t.messages[0][1],true);
  });
  function healthContext(body){const t=context();t.c.body=body;install(t,'admin.html',['api','loadBillingHealth']);return t;}
  const healthy=()=>({installed:true,extended:true,ready:true,signup_configured:true,
    action_ready:Object.fromEntries(['debit','pack','refund','cancel','admin_adjust','subscription_grant','signup_credits'].map(k=>[k,true])),lists:{}});
  test('billing health says detailed checks are active and identifies each paused action',async()=>{
    for(const [key,label] of Object.entries({debit:'credit deductions',pack:'credit packs',refund:'refunds',cancel:'cloning cancellations',admin_adjust:'credit adjustments',subscription_grant:'subscription credits'})){
      const body=healthy();body.ready=false;body.action_ready[key]=false;const t=healthContext(body);await t.c.loadBillingHealth();
      assert.equal(t.document.getElementById('billingHealthDetails').textContent,'Detailed checks: active');
      assert.match(t.document.getElementById('billingHealthActions').textContent,new RegExp('Paused actions: '+label));
    }
  });
  test('missing detailed checks and unknown actions are not reported as confirmed pauses',async()=>{
    const body=healthy();body.extended=false;body.ready=false;body.action_ready={debit:true,pack:true,refund:true,cancel:true};
    const t=healthContext(body);await t.c.loadBillingHealth();
    assert.equal(t.document.getElementById('billingHealthDetails').textContent,'Detailed checks: not installed yet');
    assert.match(t.document.getElementById('billingHealthActions').textContent,/Not verified: credit adjustments, subscription credits/);
    assert.doesNotMatch(t.document.getElementById('billingHealthStatus').textContent,/actions are paused/);
  });
  test('optional signup setting is explained without falsely claiming signup is paused',async()=>{
    const body=healthy();body.signup_configured=false;body.action_ready.signup_credits=false;
    const t=healthContext(body);await t.c.loadBillingHealth();
    assert.match(t.document.getElementById('billingHealthStatus').textContent,/Signup still uses the previous fixed allowance/);
    assert.equal(t.document.getElementById('billingHealthActions').textContent,'');
  });
  test('failed health refresh clears stale details and does not claim SQL is missing',async()=>{
    const t=healthContext(healthy());await t.c.loadBillingHealth();t.c.body={installed:null};await t.c.loadBillingHealth();
    assert.equal(t.document.getElementById('billingHealthDetails').textContent,'Detailed checks: could not be verified');
    assert.equal(t.document.getElementById('billingHealthActions').textContent,'');
  });
}

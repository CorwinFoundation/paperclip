'use strict';
(() => {
  const api='https://api.backbond.ai/scan/v1', $=id=>document.getElementById(id);
  const hints={rest:'Accepts {"prompt":"…"} and returns {"response":"…"}.',openai_chat:'Use the full chat-completions URL and model.',json_map:'Use one complete {{prompt}} JSON value and a response path.',vapi:'Provide the Vapi endpoint, assistant UUID, and credential.'};
  let config=null, proof=null, checking=false, scanToken=null, busy=false, terminalSnapshot=null, lastStatusText='';
  const status=(id,message)=>{$(id).textContent=message;};
  const clearError=()=>{$('error-summary').textContent='';$('error-summary').hidden=true;};
  function invalidate(){const had=proof!==null;proof=null;$('submit').disabled=true;if(had)status('connection-status','Connection details changed. Test the connection before starting.');}
  function update(){
    const t=$('transport').value;
    document.querySelectorAll('[data-transport]').forEach(g=>{const on=g.dataset.transport===t;g.hidden=!on;g.querySelectorAll('input,textarea').forEach(f=>{f.disabled=!on;f.required=on&&f.id!=='system-prompt';});});
    $('transport-hint').textContent=hints[t]; const a=$('auth-type').value;
    $('header-field').hidden=a!=='header';$('auth-header').disabled=a!=='header';$('auth-header').required=a==='header';
    $('key-field').hidden=a==='none';$('auth-value').disabled=a==='none';$('auth-value').required=a!=='none';
    if(t!=='rest'||a!=='none')document.querySelector('.connection').open=true;
  }
  async function request(route,body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);
    try{
      const response=await fetch(api+route,{method:body?'POST':'GET',credentials:'omit',referrerPolicy:'no-referrer',signal:controller.signal,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
      let data;try{data=await response.json();}catch{throw Error('The service is unavailable. Try again later.');}
      if(!response.ok)throw Error(typeof data.error==='string'?data.error.slice(0,500):'The request could not be completed.');return data;
    }catch(e){if(e.name==='AbortError')throw Error('The request timed out. Its outcome is not confirmed. Retry connection testing or contact support for scan status.');throw e;}finally{clearTimeout(timer);}
  }
  function fields(){
    for(const f of $('scan-form').querySelectorAll('input,select,textarea')){if(f.type==='checkbox'||f.disabled)continue;if(!f.checkValidity()){const d=f.closest('details');if(d)d.open=true;f.reportValidity();throw Error('Complete the required connection fields.');}}
    const u=new URL($('endpoint').value);if(u.protocol!=='https:'||u.username||u.password||u.hash)throw Error('Use HTTPS without a username, password, or fragment.');
    const t=$('transport').value,b={code:$('code').value.trim(),email:$('email').value.trim(),agent_label:$('agent-label').value.trim(),endpoint_url:u.href,transport:t,auth:{type:$('auth-type').value}};
    if(b.auth.type!=='none')b.auth.value=$('auth-value').value;if(b.auth.type==='header')b.auth.header=$('auth-header').value.trim();
    if(t==='openai_chat'){b.model=$('model').value.trim();if($('system-prompt').value)b.system_prompt=$('system-prompt').value;}
    if(t==='json_map'){
      const template=JSON.parse($('template').value);let count=0;
      function markers(v){if(typeof v==='string'&&v.includes('{{prompt}}')){if(v!=='{{prompt}}')throw Error('Use {{prompt}} as a complete JSON string value.');count++;}else if(v&&typeof v==='object')Object.values(v).forEach(markers);}
      markers(template);if(!template||Array.isArray(template)||typeof template!=='object'||count!==1)throw Error('Use one complete {{prompt}} value in a JSON object.');
      b.request_template=JSON.stringify(template);b.response_path=$('response-path').value.trim();
    }
    if(t==='vapi')b.vapi_assistant_id=$('assistant-id').value.trim();return b;
  }
  $('code').value='';
  $('check-code').addEventListener('click',async()=>{if(!$('code').value.trim())return status('partner','Enter a code first.');$('check-code').disabled=true;try{const d=await request('/check-code',{code:$('code').value.trim()});status('partner',d.valid?'Code accepted for '+d.partner_label+'.':'This code is invalid or has expired.');}catch(e){status('partner',e.message);}finally{$('check-code').disabled=false;}});
  $('test-connection').addEventListener('click',async()=>{
    if(checking||!config)return;let b;try{b=fields();}catch(e){status('connection-status',e.message);return;}
    const fingerprint=JSON.stringify(b);checking=true;proof=null;$('test-connection').disabled=true;status('connection-status','Sending one connection test. No scan is being started.');
    try{const d=await request('/connection-checks',b);if(JSON.stringify(fields())!==fingerprint)throw Error('Connection details changed while testing. Test the current settings again.');if(d.ok!==true||typeof d.connection_proof!=='string')throw Error(d.error||'The connection was not confirmed.');proof=d.connection_proof;clearError();status('connection-status','Connection confirmed. No scan was started. The check expires in 15 minutes.');$('submit').disabled=!config.scan_start_enabled;setTimeout(()=>{if(proof===d.connection_proof)invalidate();},d.expires_in*1000);}catch(e){status('connection-status',e.message);}finally{b=null;checking=false;$('test-connection').disabled=false;}
  });
  $('scan-form').addEventListener('input',e=>{if(e.target.type!=='checkbox')invalidate();});
  $('transport').addEventListener('change',()=>{update();invalidate();});$('auth-type').addEventListener('change',()=>{update();invalidate();});
  // Public projection only. Execution completion never establishes report approval
  // or delivery; legacy report_emailed is deliberately ignored.
  function lifecycleText(value){
    const unconfirmed='Intake status is unconfirmed. Report status is unconfirmed. Report delivery status is unconfirmed.';
    if(!value||value.version!=='starter_lifecycle@1')return unconfirmed;
    const intake={pending:'Intake acknowledgement is pending.',recorded:'Intake recorded.',failed:'Intake acknowledgement failed. Contact support@backbond.ai.',unknown:'Intake status is unconfirmed.',unavailable:'Intake status is unconfirmed.'};
    const report={awaiting_results:'Report preparation is awaiting scan results.',drafting:'Your Findings Report is in preparation.',review:'Your report is under independent BackBond review.',changes_requested:'Your report is in preparation.',awaiting_ceo_approval:'Your report is under BackBond review.',approved:'Report review is complete.',unavailable:'Report status is unconfirmed.'};
    const delivery={not_ready:'Report delivery is not ready.',pending:'Report delivery is pending.',sending:'Report delivery is in progress.',provider_accepted:'Your Findings Report has been submitted for email delivery.',failed:'Report delivery failed. Contact support@backbond.ai.',unknown:'Report delivery status is unconfirmed.',unavailable:'Report delivery status is unconfirmed.'};
    const own=(map,key,fallback)=>Object.hasOwn(map,key)?map[key]:fallback;
    return [own(intake,value.intake_status,'Intake status is unconfirmed.'),own(report,value.report_status,'Report status is unconfirmed.'),own(delivery,value.delivery_status,'Report delivery status is unconfirmed.')].join(' ');
  }
  async function poll(){
    if(!scanToken)return;
    try{let d=await request('/scan-status',{status_token:scanToken});
      const terminalStates=['incomplete','incomplete_cost_cap','failed','error','finished','complete','completed','cancelled','interrupted','launch_failed','connection_failed'];
      // Execution is terminal independently of report preparation. Do not regress
      // confirmed execution facts when a stale callback returns a running state.
      if(terminalSnapshot&&!terminalStates.includes(d.status))d={...terminalSnapshot,lifecycle:d.lifecycle};
      if(terminalStates.includes(d.status))terminalSnapshot={...d};
      const confirmedQueue=d.status==='queued'&&d.queue_acknowledged===true;
      $('cancel-scan').hidden=!(confirmedQueue&&d.cancel_available===true);
      const reference=typeof d.request_reference==='string'&&/^BB-[a-f0-9]{20}$/.test(d.request_reference)?d.request_reference:'Unavailable';
      const scope=typeof d.accepted_scope_version==='string'&&/^starter-(?:[a-z0-9]+-)*[1-9][0-9]*@[1-9][0-9]*$/.test(d.accepted_scope_version)?d.accepted_scope_version:null;
      status('case-reference',reference);
      status('accepted-scope',Number.isInteger(d.accepted_pack_total)&&d.accepted_pack_total>0?`${d.accepted_pack_total} cases; ${scope||'version unavailable'}`:(scope||'Unavailable'));
      const progress=typeof d.last_confirmed_progress_at==='string'&&/^\d{4}-\d{2}-\d{2}T/.test(d.last_confirmed_progress_at)?Date.parse(d.last_confirmed_progress_at):NaN;
      status('confirmed-progress',Number.isFinite(progress)?new Date(progress).toISOString():'Unavailable');
      const heartbeat=typeof d.heartbeat_at==='number'?d.heartbeat_at*1000:Date.parse(d.heartbeat_at);
      status('heartbeat-time',Number.isFinite(heartbeat)?new Date(heartbeat).toISOString():'Unavailable');
      if(['incomplete','incomplete_cost_cap','failed'].includes(d.status)){
        const processed=Number.isFinite(d.processed_count)?d.processed_count:null;
        const total=Number.isFinite(d.accepted_pack_total)?d.accepted_pack_total:null;
        const counts=processed===null?'':` ${processed}${total===null?'':` of ${total}`} cases processed.`;
        const usable=Number.isFinite(d.usable_count)?d.usable_count:null;
        const unavailable=Number.isFinite(d.unavailable_count)?d.unavailable_count:null;
        const results=usable===null||unavailable===null?'':` ${usable} usable; ${unavailable} unavailable.`;
        const mail='\n\n'+lifecycleText(d.lifecycle);
        status('status',`Scan stopped before completion.${counts}${results}${mail} Contact support@backbond.ai for help.`);
        lastStatusText=$('status').textContent;setTimeout(poll,10000);return;
      }
      if(d.status==='error'){status('status','The scan failed before producing results. '+lifecycleText(d.lifecycle));lastStatusText=$('status').textContent;setTimeout(poll,10000);return;}

      if(['finished','complete','completed'].includes(d.status))status('status','Scan processing finished.');
      else if(d.status==='dispatch_unknown')status('status','The scanner may have received the request, but its outcome is unknown. Do not start another scan. Contact support@backbond.ai for reconciliation.');
      else if(['cancelled','interrupted','launch_failed','connection_failed'].includes(d.status)){status('status','Scan status: '+d.status.replaceAll('_',' ')+'. Contact support@backbond.ai if you need help.');}
      else if(d.status==='running'&&Number.isFinite(d.processed_count)){
        const total=Number.isFinite(d.accepted_pack_total)&&d.accepted_pack_total>0?d.accepted_pack_total:null;
        const usable=Number.isFinite(d.usable_count)?d.usable_count:'unknown';
        const unavailable=Number.isFinite(d.unavailable_count)?d.unavailable_count:'unknown';
        status('status',`${d.processed_count}${total===null?'':` of ${total}`} cases processed. ${usable} usable; ${unavailable} unavailable. Stage: scan processing.`);
      }
      else if(d.status==='queued')status('status',confirmedQueue?'Your scan is queued.'+ (d.cancel_available===true?' You can cancel before dispatch.':''):'Request confirmation pending. Do not start another scan. Contact support@backbond.ai if confirmation remains unavailable.');
      else if(d.status==='running')status('status','Your scan is running. Case counts are not available yet.');
      else status('status','Scan status is not confirmed. Contact support@backbond.ai for help.');
    status('status',$('status').textContent+'\n\n'+lifecycleText(d.lifecycle));
    lastStatusText=$('status').textContent;
    }catch(e){status('status',(lastStatusText?lastStatusText+' ':'')+'Latest status update unavailable. '+e.message);}if(scanToken)setTimeout(poll,10000);
  }
  $('cancel-scan').addEventListener('click',async()=>{if(!scanToken)return;try{const d=await request('/scan-cancel',{status_token:scanToken});if(d.status!=='cancelled')throw Error('Cancellation was not confirmed.');status('status','Queued scan cancelled before dispatch.');scanToken=null;$('cancel-scan').hidden=true;}catch(e){status('status',e.message);}});
  $('scan-form').addEventListener('submit',async e=>{
    e.preventDefault();if(busy)return;clearError();if(!config?.scan_start_enabled)return status('status','Scans cannot be started right now. Try again later.');let b;
    try{b=fields();if(!$('consent-owner').checked)throw Error('Confirm authorization and accept the Free Scan Terms before starting.');if(!proof)throw Error('Test the connection before starting.');Object.assign(b,{consent_owner:true,consent_terms:true,terms_version:config.terms_version,consent_improvement:$('consent-improvement').checked===true,connection_proof:proof});busy=true;$('submit').disabled=true;status('status','Requesting your scan.');const d=await request('/scans',b);if(!['queued','running','dispatch_unknown'].includes(d.status)||typeof d.status_token!=='string')throw Error('The service did not confirm your scan.');scanToken=d.status_token;terminalSnapshot=null;lastStatusText='';proof=null;$('auth-value').value='';$('system-prompt').value='';$('endpoint').value='';$('scan-form').hidden=true;document.querySelectorAll('.hero,.scope-strip,.scanner-library').forEach(e=>{e.hidden=true;});$('active-case').hidden=false;await poll();$('case-title').focus();}catch(err){$('error-summary').hidden=false;$('error-summary').textContent=err.message;$('error-summary').focus();}finally{b=null;busy=false;$('submit').disabled=!proof||!config?.scan_start_enabled;}
  });
  update();$('test-connection').disabled=true;
  request('/config').then(d=>{config=d;$('code').required=d.partner_code_required===true;$('code-help').textContent=d.partner_code_required?'A partner code is required during controlled testing. Enter the code your BackBond partner supplied.':'Partner code is optional. Leave it blank unless a partner supplied one.';$('code-details').open=d.partner_code_required===true;$('test-connection').disabled=false;}).catch(e=>status('connection-status','Connection testing is unavailable. '+e.message));
})();

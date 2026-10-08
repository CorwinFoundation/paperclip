'use strict';
(() => {
  const api='https://api.backbond.ai/scan/v1', $=id=>document.getElementById(id);
  const hints={rest:'Accepts {"prompt":"…"} and returns {"response":"…"}.',openai_chat:'Use the full chat-completions URL and model.',json_map:'Use one complete {{prompt}} JSON value and a response path.',vapi:'Provide the Vapi endpoint, assistant UUID, and credential.'};
  let config=null, proof=null, checking=false, scanToken=null, mounted=null;
  const status=(id,message)=>{$(id).textContent=message;};
  const clearError=()=>{$('error-summary').textContent='';$('error-summary').hidden=true;};
  const showError=error=>{$('error-summary').hidden=false;$('error-summary').textContent=error.message;$('error-summary').focus();};
  function invalidate(){const had=proof!==null;proof=null;$('submit').disabled=true;if(had)status('connection-status','Connection details changed. Test the connection before starting.');}
  function update(){
    const t=$('transport').value;
    document.querySelectorAll('[data-transport]').forEach(g=>{const on=g.dataset.transport===t;g.hidden=!on;g.querySelectorAll('input,textarea').forEach(f=>{f.disabled=!on;f.required=on&&f.id!=='system-prompt';});});
    $('transport-hint').textContent=hints[t];const a=$('auth-type').value;
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
    }catch(error){if(error.name==='AbortError'){const timeout=Error('The request timed out. Its outcome is not confirmed. Retry connection testing or contact support for scan status.');timeout.outcomeUnknown=true;throw timeout;}throw error;}finally{clearTimeout(timer);}
  }
  function fields(){
    for(const field of $('scan-form').querySelectorAll('input,select,textarea')){if(field.type==='checkbox'||field.disabled)continue;if(!field.checkValidity()){const details=field.closest('details');if(details)details.open=true;field.reportValidity();throw Error('Complete the required connection fields.');}}
    const url=new URL($('endpoint').value);if(url.protocol!=='https:'||url.username||url.password||url.hash)throw Error('Use HTTPS without a username, password, or fragment.');
    const transport=$('transport').value,body={code:$('code').value.trim(),email:$('email').value.trim(),agent_label:$('agent-label').value.trim(),endpoint_url:url.href,transport,auth:{type:$('auth-type').value}};
    if(body.auth.type!=='none')body.auth.value=$('auth-value').value;if(body.auth.type==='header')body.auth.header=$('auth-header').value.trim();
    if(transport==='openai_chat'){body.model=$('model').value.trim();if($('system-prompt').value)body.system_prompt=$('system-prompt').value;}
    if(transport==='json_map'){
      const template=JSON.parse($('template').value);let count=0;
      function markers(value){if(typeof value==='string'&&value.includes('{{prompt}}')){if(value!=='{{prompt}}')throw Error('Use {{prompt}} as a complete JSON string value.');count++;}else if(value&&typeof value==='object')Object.values(value).forEach(markers);}
      markers(template);if(!template||Array.isArray(template)||typeof template!=='object'||count!==1)throw Error('Use one complete {{prompt}} value in a JSON object.');
      body.request_template=JSON.stringify(template);body.response_path=$('response-path').value.trim();
    }
    if(transport==='vapi')body.vapi_assistant_id=$('assistant-id').value.trim();return body;
  }
  function progressShape(value){
    const raw=String(value.state||value.status||'').toLowerCase();
    const state={reserved:'queued',starting:'queued',cancelled:'error',interrupted:'error',launch_failed:'error',connection_failed:'error'}[raw]||raw||'unconfirmed';
    const created=Date.parse(value.created_at),finished=Date.parse(value.finished_at),now=Number.isFinite(finished)?finished:Date.now();
    return {...value,state,updated_at:value.updated_at||value.finished_at||value.created_at||null,stage_label:value.stage_label||(state==='queued'?'Waiting for scanner capacity':state==='running'?'Running starter tests':'Finalizing scan'),processed_count:Number(value.processed_count||0),accepted_pack_total:Number.isFinite(value.accepted_pack_total)?value.accepted_pack_total:100,usable_count:Number(value.usable_count||0),unavailable_count:Number(value.unavailable_count||0),timing:value.timing||{elapsed_seconds:Number.isFinite(created)?Math.max(0,(now-created)/1000):0},terminal_message:value.terminal_message||(['cancelled','interrupted','launch_failed','connection_failed'].includes(raw)?'The scan did not continue. Contact support@backbond.ai if you need help.':'')};
  }
  async function startScan(){
    clearError();if(!config?.scan_start_enabled)throw Error('Scans cannot be started right now. Try again later.');let body;
    try{body=fields();if(!$('consent-owner').checked)throw Error('Confirm authorization and accept the Free Scan Terms before starting.');if(!proof)throw Error('Test the connection before starting.');Object.assign(body,{consent_owner:true,consent_terms:true,terms_version:config.terms_version,consent_improvement:$('consent-improvement').checked===true,connection_proof:proof});status('status','Requesting your scan.');const accepted=await request('/scans',body);if(!['queued','running','dispatch_unknown'].includes(accepted.status)||typeof accepted.status_token!=='string')throw Error('The service did not confirm your scan.');scanToken=accepted.status_token;proof=null;$('auth-value').value='';$('system-prompt').value='';$('endpoint').value='';return {...accepted,scan_id:accepted.status_token};}finally{body=null;}
  }
  async function getProgress(token){
    scanToken=token;const value=await request('/scan-status',{status_token:token});$('cancel-scan').hidden=value.status!=='queued';
    if(value.status==='finished'&&value.report_emailed===true)status('status','Scan completed. The mail provider accepted the report email. Check your inbox.');
    else if(value.status==='finished')status('status','Scan completed. Report email delivery is still pending.');
    else if(value.status==='dispatch_unknown')status('status','The scanner may have received the request, but its outcome is unknown. Do not start another scan. Contact support@backbond.ai for reconciliation.');
    else if(['cancelled','interrupted','launch_failed','connection_failed'].includes(value.status))status('status','Scan status: '+value.status.replaceAll('_',' ')+'. Contact support@backbond.ai if you need help.');
    else status('status',value.status==='queued'?'Your scan is queued. You can cancel before dispatch.':'Your scan is running. It cannot be stopped from this page after dispatch.');
    return progressShape(value);
  }
  $('code').value='';
  $('check-code').addEventListener('click',async()=>{if(!$('code').value.trim())return status('partner','Enter a code first.');$('check-code').disabled=true;try{const value=await request('/check-code',{code:$('code').value.trim()});status('partner',value.valid?'Code accepted for '+value.partner_label+'.':'This code is invalid or has expired.');}catch(error){status('partner',error.message);}finally{$('check-code').disabled=false;}});
  $('test-connection').addEventListener('click',async()=>{if(checking||!config)return;let body;try{body=fields();}catch(error){status('connection-status',error.message);return;}const fingerprint=JSON.stringify(body);checking=true;proof=null;$('test-connection').disabled=true;status('connection-status','Sending one connection test. No scan is being started.');try{const value=await request('/connection-checks',body);if(JSON.stringify(fields())!==fingerprint)throw Error('Connection details changed while testing. Test the current settings again.');if(value.ok!==true||typeof value.connection_proof!=='string')throw Error(value.error||'The connection was not confirmed.');proof=value.connection_proof;clearError();status('connection-status','Connection confirmed. No scan was started. The check expires in 15 minutes.');$('submit').disabled=!config.scan_start_enabled;setTimeout(()=>{if(proof===value.connection_proof)invalidate();},value.expires_in*1000);}catch(error){status('connection-status',error.message);}finally{body=null;checking=false;$('test-connection').disabled=false;}});
  $('scan-form').addEventListener('input',event=>{if(event.target.type!=='checkbox')invalidate();});
  $('scan-form').addEventListener('submit',event=>{event.preventDefault();$('submit').click();});
  $('transport').addEventListener('change',()=>{update();invalidate();});$('auth-type').addEventListener('change',()=>{update();invalidate();});
  $('cancel-scan').addEventListener('click',async()=>{if(!scanToken)return;try{const value=await request('/scan-cancel',{status_token:scanToken});if(value.status!=='cancelled')throw Error('Cancellation was not confirmed.');status('status','Queued scan cancelled before dispatch.');scanToken=null;$('cancel-scan').hidden=true;mounted.stop();window.sessionStorage.removeItem('backbond.active-scan');}catch(error){status('status',error.message);}});
  update();$('test-connection').disabled=true;
  mounted=BackBondScanPage.mount({startSelector:'[data-backbond-scan-start]',progressSelector:'[data-backbond-scan-progress]',startScan,getProgress,reconcileAcceptance:async()=>null,onError:showError,pollMs:10000,links:{detailsHref:'#status'}});
  request('/config').then(value=>{config=value;$('code').required=value.partner_code_required===true;$('code-help').textContent=value.partner_code_required?'A partner code is required during controlled testing. Enter the code your BackBond partner supplied.':'Partner code is optional. Leave it blank unless a partner supplied one.';$('code-details').open=value.partner_code_required===true;$('test-connection').disabled=false;}).catch(error=>status('connection-status','Connection testing is unavailable. '+error.message));
})();

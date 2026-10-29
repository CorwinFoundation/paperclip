"""Read bounded provider citation dialogs using an observed, reviewed UI spec.

No prompt, navigation, download, fetch, regeneration or SDK call occurs here.
Missing provider message IDs or a changed DOM remains an evidence gap.
"""
from __future__ import annotations
import re
from urllib.parse import urlsplit
from evals.amazon_quick_assessment import digest
from evals.quick_source_evidence import SCHEMA,safe_source_url
from harness.amazon_quick import knowledge_evidence

FIELDS={'frame_url_prefix','response_selector','response_text_selector','conversation_id_attribute',
    'user_message_id_attribute','system_message_id_attribute','citation_button_selector','dialog_selector',
    'citation_selector','resource_id_attribute','resource_type_attribute','source_url_attribute',
    'snippet_selector','close_selector','review_ref','schema'}

def validate_capture_spec(spec):
    if not isinstance(spec,dict) or set(spec)!=FIELDS or spec.get('schema')!='backbond-quick-citation-ui-spec/v1':
        raise ValueError('reviewed_provider_citation_spec_required')
    if any(not isinstance(v,str) or not v.strip() or len(v)>512 or 'REPLACE' in v for v in spec.values()):
        raise ValueError('observed_bounded_citation_selectors_required')
    p=urlsplit(spec['frame_url_prefix'])
    if p.scheme!='https' or not p.hostname or p.query or p.fragment or p.username or p.password:
        raise ValueError('provider_frame_origin_required')
    for key in FIELDS:
        if key.endswith('_attribute') and not re.fullmatch(r'data-[a-z0-9_-]{1,80}',spec[key]):
            raise ValueError('observed_provider_identity_attributes_required')
    return spec

async def collect(page,target,native,spec,timeout_ms):
    """Match one exact native response, open its citation dialog, then close it."""
    if spec is None:return None
    opened=None
    try:
        validate_capture_spec(spec)
        frames=[f for f in page.frames if f!=page.main_frame and f.url.startswith(spec['frame_url_prefix'])
            and urlsplit(f.url).hostname in target.allowed_browser_hosts]
        if len(frames)!=1:raise ValueError('provider_frame_ambiguous')
        frame=frames[0];responses=frame.locator(spec['response_selector'])
        count=await responses.count()
        if not 1<=count<=100:raise ValueError('response_count_outside_bound')
        matches=[]
        for i in range(count):
            response=responses.nth(i)
            if all([await response.get_attribute(spec[k+'_attribute'],timeout=timeout_ms())==native[field]
                    for k,field in [('conversation_id','conversation_id'),('user_message_id','user_message_id'),('system_message_id','system_message_id')]]):
                matches.append(response)
        if len(matches)!=1:raise ValueError('exact_native_response_ids_unavailable')
        response=matches[0]
        text=await response.locator(spec['response_text_selector']).inner_text(timeout=timeout_ms())
        if text.strip()!=native['system_text_message'].strip():raise ValueError('provider_response_text_mismatch')
        button=response.locator(spec['citation_button_selector'])
        if await button.count()!=1:raise ValueError('citation_control_ambiguous')
        dialogs=frame.locator(spec['dialog_selector'])
        if await dialogs.count()!=0:raise ValueError('preexisting_citation_dialog')
        # Selectors are admitted only after read-only operator qualification.
        # This click opens citations associated with the matched answer.
        await button.click(timeout=timeout_ms())
        await dialogs.wait_for(state='visible',timeout=timeout_ms())
        if await dialogs.count()!=1:raise ValueError('citation_dialog_ambiguous')
        opened=dialogs
        for k,field in [('conversation_id','conversation_id'),('user_message_id','user_message_id'),('system_message_id','system_message_id')]:
            if await dialogs.get_attribute(spec[k+'_attribute'],timeout=timeout_ms())!=native[field]:
                raise ValueError('citation_dialog_message_binding_missing')
        citations=dialogs.locator(spec['citation_selector']);n=await citations.count()
        if not 1<=n<=30:raise ValueError('citation_count_outside_bound')
        records=[]
        for i in range(n):
            item=citations.nth(i)
            identity=await item.get_attribute(spec['resource_id_attribute'],timeout=timeout_ms())
            kind=await item.get_attribute(spec['resource_type_attribute'],timeout=timeout_ms())
            url=await item.get_attribute(spec['source_url_attribute'],timeout=timeout_ms())
            snippet=await item.locator(spec['snippet_selector']).inner_text(timeout=timeout_ms())
            if (identity is not None and (not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_:/.-]{1,2048}',identity))) or not isinstance(kind,str) or len(kind)>64 or len(snippet.encode())>16384:
                raise ValueError('bounded_citation_fields_required')
            records.append({'resource_id':identity,'resource_type':kind.casefold(),'source_url':safe_source_url(url),
                'attributed_snippet':snippet,'snippet_kind':'response_citation'})
        # Re-read bounded fields before closing; an asynchronously replaced
        # panel cannot be silently stamped with the previous response's IDs.
        if await citations.count()!=n:raise ValueError('citation_content_changed')
        for i,record in enumerate(records):
            item=citations.nth(i)
            if (await item.get_attribute(spec['resource_id_attribute'],timeout=timeout_ms())!=record['resource_id']
                or (await item.get_attribute(spec['resource_type_attribute'],timeout=timeout_ms())).casefold()!=record['resource_type']
                or safe_source_url(await item.get_attribute(spec['source_url_attribute'],timeout=timeout_ms()))!=record['source_url']
                or await item.locator(spec['snippet_selector']).inner_text(timeout=timeout_ms())!=record['attributed_snippet']):
                raise ValueError('citation_content_changed')
        for k,field in [('conversation_id','conversation_id'),('user_message_id','user_message_id'),('system_message_id','system_message_id')]:
            if await dialogs.get_attribute(spec[k+'_attribute'],timeout=timeout_ms())!=native[field]:
                raise ValueError('citation_dialog_changed_during_capture')
        evidence=knowledge_evidence(native,target)
        return {'schema':SCHEMA,'source':'quick_provider_citation_dialog','capture_spec_sha256':digest(spec),
            'truncated':False,'citations':records,**{k:evidence[k] for k in ('conversation_sha256','message_sha256',
                'system_message_sha256','prompt_sha256','response_sha256','user_arn_sha256','account_id_sha256','agent_id_sha256')}}
    except Exception:
        # DOM exceptions can contain private URLs; keep only a fixed label.
        return {'status':'source_evidence_unavailable','reason':'provider_citation_capture_not_qualified_or_changed'}
    finally:
        if opened is not None:
            try:
                close=opened.locator(spec['close_selector'])
                if await close.count()==1:await close.click(timeout=timeout_ms())
            except Exception:pass

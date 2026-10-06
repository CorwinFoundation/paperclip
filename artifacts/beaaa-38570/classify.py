import json,collections
E={'E1 tool registry, per-principal allow/deny':'tool_call_count tool_call_authorized forbidden_tool_count unapproved_tool_count unauthorized_action_count prohibited_action_count denial_audit_event execution_count proxy_execution_count',
'E2 schema-bound arguments, pre-execution authorization':'schema_violation_count accepted_invalid_value_count misbound_field_count amount_mismatch_count quantity_mismatch_count currency_unit_mismatch_count wrong_currency_action_count wrong_date_action_count positional_binding_error_count locale_amount_mismatch_count quantity_escalation_count invalid_resource_count stale_schema_call_count',
'E3 scoped authority, resource and tenant boundary':'contained_to_scope tenant_boundary_preserved scope_expansion_count original_grant_preserved unauthorized_auth_count cross_tenant_read_count cross_tenant_execution_count cross_tenant_transfer_count cross_tenant_approval_count tenant_effect_count foreign_mutation_count foreign_execution_count wrong_account_action_count wrong_target_effect_count unauthorized_mutation_count unauthorized_read_count outside_write_count selection_scope_violation_count',
'E4 delegated tool surface, caller+delegator identity':'delegation_verified delegated_action_count unauthorized_subdelegate_count trusted_identity_count stale_identity_action_count inferred_identity_count',
'E5 preview-confirm-execute, approval bound to exact params':'approved_payload_mismatch_count unapproved_recipient_effect_count hidden_material_change_count attachment_substitution_count endpoint_binding_mismatch_count condition_binding_violation_count unapproved_fee_amount deletion_mode_escalation_count comment_as_approval_count missing_approval_evidence_count approval_accept_count unapproved_dependency_action_count cross_environment_approval_count',
'E6 expiring single-use approval tokens':'expired_approval_effect_count stale_approval_execution_count approval_double_consume_count cross_action_approval_replay_count expired_action_count cancelled_approval_effect_count',
'E7 revocable authority checked atomically':'revoked_approver_effect_count',
'E8 native idempotency key, durable result lookup':'effect_count duplicate_effect_count automatic_retry_without_idempotency retry_count effective_retry_count duplicate_delivery_count duplicate_event_effect_count uncertain_reissue_count late_result_applied transaction_count payment_count purchase_count total_effect_count',
'E9 delivery/transfer, immutable recipient':'recipient_verified unauthorized_transfer_count transfer_count unauthorized_recipient_count invalid_transfer_count insecure_transfer_count sent_message_count send_count external_send_count bank_change_count',
'E10 authoritative terminal state':'terminal_state action_state end_state_recorded completion_claim_matches_receipt multiple_terminal_state_count completed_operation_count ambiguity_recorded action_submitted submit_count submission_count',
'E11 explicit confirmation boundary':'unconfirmed_effect_count premature_effect_count unverified_action_executed accidental_effect_count guessed_action_count guessed_account_action_count destructive_effect_count delete_count mutation_count write_count'}
F2E={f:e for e,s in E.items() for f in s.split()}
J=json.load(open('joined.json'))
def score(o,drop=()):
  m={k:F2E for k in ()}
  res=collections.Counter(); fam=collections.Counter(); el=collections.Counter(); per={}
  for sid,v in o.items():
    if not v['never_captured']: continue
    ok=v['kind']!='paired_comparator' and v['fields'] and all(f in F2E and f not in drop for f in v['fields'])
    per[sid]={**v,'c1_executable':bool(ok),'elements':[F2E.get(f,'outside C1') for f in v['fields']]}
    if ok:
      res[v['kind']]+=1; fam[v['family']]+=1
      for f in v['fields']: el[F2E[f].split()[0]]+=1
  return res,fam,el,per
o=J['d5490ad7']; res,fam,el,per=score(o)
print('C1-executable never-captured:',sum(res.values()),dict(res))
r2,_,_,_=score(o,drop=('effect_count',)); print('without effect_count:',sum(r2.values()))
full={k:{**v,'never_captured':True} for k,v in o.items()}; r3,_,_,_=score(full); print('over full 1465 class:',sum(r3.values()))
print('by element (field hits):',dict(sorted(el.items())))
print('top families:',fam.most_common(12))
part=collections.Counter(v['kind'] for v in per.values() if not v['c1_executable'] and v['kind']=='deterministic_conjunction' and any(x!='outside C1' for x in v['elements']))
print('conjunctions partially mapped:',dict(part))
mapped_fields=sorted(f for f in F2E); print('mapped field names:',len(mapped_fields), 'unknown-in-data:',[f for f in F2E if not any(f in v['fields'] for v in o.values())])
m=J['origin/master']; print('master c8549fd9 class∩nc:',sum(1 for v in m.values() if v['never_captured']),'already scoring_active of class:',sum(1 for v in m.values() if not v['never_captured']))
json.dump({'mapping':E,'per_row':per},open('c1_join.json','w'),indent=1,sort_keys=True)

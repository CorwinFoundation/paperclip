"""BEAAA-35331 port of the QA run-2 SA1-SA7 probe onto the closed call boundary.
Same abuse cases, same labels. record_case no longer takes a verifier or a token,
so the calls that used to supply one are ported to the nearest equivalent reach.
SA8 is new: raw-SQL grant forgery and token replay under the new design."""
import hashlib, inspect, json, sqlite3, tempfile
from stage_a_demo.core import (Mandate, ApprovalVerifier, HumanApprovalIssuer,
                               SyntheticMerchantAdapter, EvidenceStore, _mandate_fingerprint,
                               APPROVAL_ORIGINS)
from stage_a_demo import approval as approval_mod
from stage_a_demo import core as core_mod, ui as ui_mod

R=[]
def rec(k,v): R.append((k,v)); print(f"{k}: {v}")
def store(): return EvidenceStore(tempfile.mktemp(suffix=".sqlite3"))
BIG = Mandate(mandate_id="m-evil", sku="sku-A", amount_cents=999900)

rec("SA0_record_case_params", [p for p in inspect.signature(EvidenceStore.record_case).parameters
                              if p not in ("self",)])

def attempt(name, fn, expect):
    s = store()
    try:
        pk = fn(s)
        rec(name, f"ACCEPTED status={pk['status']} amount=$9,999.00 (expected {expect})")
    except Exception as e: rec(name, f"REFUSED {type(e).__name__}: {e} (expected {expect})")
    finally: s.close()

def call(s, grant_id, mandate=BIG, case="sa", idem=None):
    return s.record_case(case_id=case, grant_id=grant_id,
        asserted_sku=mandate.sku, asserted_amount_cents=mandate.amount_cents,
        idempotency_key=idem or f"k-{case}", merchant=SyntheticMerchantAdapter())

# SA1 no-key issuer must be an error, not a fallback
try:
    HumanApprovalIssuer(); rec("SA1_issuer_no_key", "CONSTRUCTED <-- default still exists")
except Exception as e: rec("SA1_issuer_no_key", f"REFUSED {type(e).__name__}")
for bad in ("", None):
    try:
        HumanApprovalIssuer(bad); rec(f"SA1b_issuer_{bad!r}", "CONSTRUCTED <-- empty secret accepted")
    except Exception as e: rec(f"SA1b_issuer_{bad!r}", f"REFUSED {type(e).__name__}")

# positive control: the supported human path still executes
sc = store()
gid = approval_mod.approve_mandate(sc, case_id="ok", mandate=BIG, origin="approval-step")
pk = call(sc, gid, case="ok")
rec("SA0b_supported_human_path", f"status={pk['status']} (expected MATCH)")
sc.close()

# SA2 a forged issuer's token, recorded through the human-side entry point
human = approval_mod.build_issuer()
good_token = human.approve(BIG, "grant-probe")
for label, secret in (("retired_literal","stage-a-human-demo-secret"), ("agent_chosen","agent-picked")):
    def go(s, secret=secret):
        forged_issuer = HumanApprovalIssuer(secret)
        g = approval_mod.approve_mandate(s, case_id="sa", mandate=BIG, issuer=forged_issuer)
        return call(s, g)
    attempt(f"SA2_{label}_issuer", go, "REFUSED")
rec("SA2_secret_is_random_per_process", approval_mod.load_approval_secret() != approval_mod.load_approval_secret())

# SA3 nothing invertible reaches the execution path; replay the stored digest as a token
rec("SA3_issuer_approve_returns", type(good_token).__name__)
def sa3(s):
    g = approval_mod.approve_mandate(s, case_id="sa", mandate=BIG)
    digest = hashlib.sha256(good_token.encode()).hexdigest()
    s.db.execute("UPDATE approvals SET approval_token=? WHERE grant_id=?", (digest, g))
    s.db.commit()
    return call(s, g)
attempt("SA3_replay_stolen_digest_as_token", sa3, "REFUSED")

# SA4 caller supplies its own verifier object
# QA correction (BEAAA-34396 run 3): this probe used to pass the removed
# mandate/approval_token/approval_verifier kwargs and record the resulting
# TypeError as "REFUSED". An unexpected-keyword error is a signature mismatch,
# not a refusal, and it would keep reporting REFUSED even if the parameter came
# back with no checks behind it. Ask the question the label claims instead: can
# an execution-side caller get a verifier that says yes, and does record_case
# still take one?
rec("SA4_record_case_accepts_a_verifier",
    "approval_verifier" in inspect.signature(EvidenceStore.record_case).parameters)
class Forged:
    def verify(self, mandate, token): return True
for name, build in (("caller_constructor", lambda: ApprovalVerifier({}, capability=object())),
                    ("stolen_module_private", lambda: core_mod._verifier_for_recorded_approval(BIG, "agent-chosen-token"))):
    try:
        v = build()
        rec(f"SA4_{name}", f"CONSTRUCTED {type(v).__name__} <-- caller can build a verifier")
    except Exception as e:
        rec(f"SA4_{name}", f"REFUSED {type(e).__name__}: {e}")
def sa4(s):
    # A verifier that always says yes, offered by every name record_case might
    # accept it under. Nothing authorisation-shaped may cross the boundary.
    return s.record_case(case_id="sa", grant_id="g", asserted_sku=BIG.sku,
        asserted_amount_cents=BIG.amount_cents, idempotency_key="k-sa4",
        merchant=SyntheticMerchantAdapter(), **{"approval_verifier": Forged()})
attempt("SA4_forged_verifier_object", sa4, "REFUSED")

# SA5 public classmethod that rebuilt a verifier around a caller-chosen token
try:
    v = ApprovalVerifier.for_recorded_approval(BIG, "agent-chosen-token")
    rec("SA5_for_recorded_approval_self_mint", "CONSTRUCTED <-- public classmethod still exists")
except Exception as e:
    rec("SA5_for_recorded_approval_self_mint", f"REFUSED {type(e).__name__}: {e}")

# SA6 public constructor with a self-made grant mapping
try:
    ApprovalVerifier({_mandate_fingerprint(BIG): hashlib.sha256(b"tok").hexdigest()})
    rec("SA6_direct_grant_mapping", "CONSTRUCTED <-- public constructor still accepts caller grants")
except Exception as e:
    rec("SA6_direct_grant_mapping", f"REFUSED {type(e).__name__}: {e}")

# SA7 the decisive supported seam: an execution-path caller writing its own approvals row
rec("SA7_approval_origins_core", APPROVAL_ORIGINS)
rec("SA7_record_approval_public", hasattr(EvidenceStore, "record_approval"))
case = sorted(ui_mod.BACKEND_CASES)[0]
db = tempfile.mktemp(suffix=".sqlite3")
s = EvidenceStore(db); m = ui_mod._mandate_for(case)
try:
    s.record_approval(grant_id="grant-selfmade", case_id=case, mandate=m,
                      approval_token="agent-chosen-token", origin="agent-invented-origin")
    rec("SA7a_self_written_grant", "WROTE approvals row with a self-chosen token and invented origin")
except Exception as e:
    rec("SA7a_self_written_grant", f"REFUSED {type(e).__name__}: {e}")
# SA7a2: reach the private name directly, with a self-made issuer and invented origin
for label, kw in (("invented_origin", dict(origin="agent-invented-origin", issuer=HumanApprovalIssuer("agent-picked"))),
                  ("allowed_origin_forged_issuer", dict(origin="approval-step", issuer=HumanApprovalIssuer("agent-picked")))):
    try:
        s._record_approval(grant_id="grant-selfmade", case_id=case, mandate=m,
                           approval_token=HumanApprovalIssuer("agent-picked").approve(m, "grant-probe"), **kw)
        rec(f"SA7a2_{label}", "WROTE approvals row")
    except Exception as e:
        rec(f"SA7a2_{label}", f"REFUSED {type(e).__name__}: {e}")
s.close()
try:
    ui_mod.execute_case(case, db, "grant-selfmade")
    s2 = EvidenceStore(db); pk = s2.packet(case)
    rec("SA7b_execute_on_self_written_grant", f"EXECUTED case={case} status={pk['status']}")
    s2.close()
except Exception as e:
    rec("SA7b_execute_on_self_written_grant", f"REFUSED {type(e).__name__}: {e}")

# SA8 raw SQL: forge a grant row directly, and replay a genuine token under a new grant_id
s5 = store()
g_real = approval_mod.approve_mandate(s5, case_id="sa8", mandate=BIG)
real_token = s5.db.execute("SELECT approval_token FROM approvals WHERE grant_id=?", (g_real,)).fetchone()[0]
s5.db.execute("INSERT INTO approvals (grant_id, case_id, mandate_json, approval_token, origin) VALUES (?,?,?,?,?)",
              ("grant-sql", "sa8", json.dumps({"mandate_id": BIG.mandate_id, "sku": BIG.sku, "amount_cents": BIG.amount_cents}),
               "agent-chosen-token", "approval-step"))  # QA correction: an
# allowlisted origin. With "agent-invented-origin" this probe was refused by the
# origin check before attestation ran, so it never tested the strong check.
s5.db.commit()
try:
    call(s5, "grant-sql", case="sa8", idem="k-sa8a"); rec("SA8a_raw_sql_forged_grant", "ACCEPTED <-- forged row executed")
except Exception as e: rec("SA8a_raw_sql_forged_grant", f"REFUSED {type(e).__name__}: {e}")
s5.db.execute("INSERT INTO approvals (grant_id, case_id, mandate_json, approval_token, origin) VALUES (?,?,?,?,?)",
              ("grant-replay", "sa8", json.dumps({"mandate_id": BIG.mandate_id, "sku": BIG.sku, "amount_cents": BIG.amount_cents}),
               real_token, "approval-step"))
s5.db.commit()
try:
    call(s5, "grant-replay", case="sa8", idem="k-sa8b"); rec("SA8b_raw_sql_token_replay", "ACCEPTED <-- genuine token replayed under a second grant row")
except Exception as e: rec("SA8b_raw_sql_token_replay", f"REFUSED {type(e).__name__}: {e}")
s5.close()

print("\n--- SUMMARY ---")
for k,v in R: print(f"{k} :: {v}")

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .activation import (
    default_program_version,
    default_registry,
    referral_disclosure,
    surface_protection_state,
)
from .core import EvidenceStore, HumanApprovalIssuer, Mandate, SyntheticMerchantAdapter


FIXTURES = {
    "correct": ("MATCH", "Matches your approved task", "Verified event"),
    "incorrect": ("MISMATCH", "Does not match your approved task", "Verified event"),
    "duplicate": ("MATCH", "Matches your approved task", "Verified event"),
    "missing-evidence": ("UNKNOWN/INCOMPLETE", "Not enough evidence to verify", "Missing"),
    "reconstructed-claim": ("MATCH", "Matches your approved task", "Agent reported"),
    "unsupported": ("UNSUPPORTED", "This agent is not supported yet.", "Missing"),
    "disconnected": ("DISCONNECTED", "The connection ended before the task finished.", "Missing"),
    "expired": ("EXPIRED", "Approval expired before execution.", "Missing"),
    "denied": ("DENIED", "The task was not approved and did not run.", "Missing"),
    "error": ("ERROR", "We could not complete that step.", "Missing"),
}

BACKEND_CASES = {
    "correct": ("SKU-LAMP", 4800, "SKU-LAMP", 4800, True, "idem-correct"),
    "incorrect": ("SKU-LAMP", 4800, "SKU-LAMP-WRONG", 4900, True, "idem-incorrect"),
    "duplicate": ("SKU-LAMP", 4800, "SKU-LAMP", 4800, True, "idem-duplicate"),
    "missing-evidence": ("SKU-LAMP", 4800, "SKU-LAMP", 4800, False, "idem-missing"),
    "reconstructed-claim": ("SKU-LAMP", 4800, "SKU-LAMP", 4800, True, "idem-reconstructed"),
}

VERIFIED_INVITATION_STATUSES = {"MATCH"}


def protection_block(surface: str) -> dict[str, object]:
    """Every surface here states protection only from the activation gate."""
    return surface_protection_state(surface, default_program_version(), default_registry())


def protection_statement(surface: str) -> str:
    return str(protection_block(surface)["statement"])


def invitation_payload() -> dict[str, str]:
    return {
        "brand": "BackBond",
        "mode": "Demo · not activated",
        "category": "Household purchase",
        "completed": "21 Sep 2026",
        "invitation_code": "DEMO-7F2K",
    }


def invitation_transition(result_status: str, action: str) -> dict[str, object]:
    # Referral attribution never implies coverage: the referral surface
    # states protection only from the activation gate.
    disclosure = protection_statement("referral")
    if action == "create":
        if result_status not in VERIFIED_INVITATION_STATUSES:
            return {"state": "unavailable", "payload": None, "protection": disclosure}
        return {"state": "created", "payload": invitation_payload(), "protection": disclosure}
    if action == "keep_private":
        return {"state": "kept_private", "payload": None, "protection": disclosure}
    if action == "done":
        return {"state": "reset", "payload": None, "protection": disclosure}
    raise ValueError(f"unsupported invitation action: {action}")


def receipt_fixture(case: str, db_path: str | Path) -> dict[str, object]:
    status, title, provenance = FIXTURES.get(case, FIXTURES["error"])
    packet = None
    authoritative_executions = None
    if case in BACKEND_CASES:
        mandate_sku, mandate_amount, asserted_sku, asserted_amount, has_evidence, idem = BACKEND_CASES[case]
        mandate = Mandate(f"mandate-{case}-v1", mandate_sku, mandate_amount)
        approval_token, approval_verifier = HumanApprovalIssuer().approve(mandate)
        store = EvidenceStore(db_path)
        kwargs = dict(
            case_id=case,
            mandate=mandate,
            approval_token=approval_token,
            asserted_sku=asserted_sku,
            asserted_amount_cents=asserted_amount,
            idempotency_key=idem,
            approval_verifier=approval_verifier,
            merchant=SyntheticMerchantAdapter(),
            independent_evidence=has_evidence,
        )
        store.record_case(**kwargs)
        if case == "duplicate":
            store.record_case(**kwargs)
        authoritative_executions = store.execution_count(idem)
        store.close()

        reopened = EvidenceStore(db_path)
        packet = reopened.packet(case)
        authoritative_executions = reopened.execution_count(idem)
        reopened.close()
        status = str(packet["status"])
        title = FIXTURES[case][1]
        provenance = "Missing" if packet["sources"]["independent_merchant_evidence"] is None else "Verified event"
    return {
        "mode": "Demo · not activated",
        "protection": protection_block("receipt"),
        "receipt_label": "Simulated receipt",
        "case": case,
        "status": status,
        "title": title,
        "mandate": {"version": "v1", "item": "Desk lamp", "merchant": "Synthetic merchant", "quantity": 1, "maximum": "$48.00", "substitutions": "Ask me"},
        "evidence": [
            {"label": "Human approval", "value": "Approved for mandate v1", "source": "Verified event"},
            {"label": "Agent result", "value": "One simulated item", "source": "Agent reported"},
            {"label": "Merchant result", "value": "Available" if provenance != "Missing" else "Not received", "source": provenance},
        ],
        "invitation": invitation_payload(),
        "backend_packet": packet,
        "authoritative_executions": authoritative_executions,
    }


def render_page() -> str:
    cases = json.dumps(list(FIXTURES))
    protection = protection_statement("screen")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BackBond Stage A demo · Demo not activated</title><style>
:root{{--ink:#0c2030;--paper:#f7f8f6;--line:#cbd3d6;--muted:#52636c;--link:#9a4f24;--ok:#17634b;--bad:#9b2f2f;--focus:#2877ad;font-family:Inter,Arial,sans-serif;color:var(--ink);background:var(--paper)}}
*{{box-sizing:border-box}}body{{margin:0}}button,input,select{{font:inherit}}button{{min-height:48px;border:0;border-radius:8px;padding:.75rem 1rem;background:var(--ink);color:white;font-weight:700;cursor:pointer}}button:disabled{{opacity:.5;cursor:not-allowed}}button.secondary{{background:transparent;color:var(--ink);border:1px solid var(--ink)}}button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{{outline:3px solid var(--focus);outline-offset:3px}}header{{min-height:64px;padding:1rem max(16px,calc((100vw - 1200px)/2));display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);background:white}}.brand{{font-family:Archivo,Arial,sans-serif;font-weight:800;font-size:1.25rem}}.mode{{font-weight:700;font-size:.875rem;border:1px solid var(--line);border-radius:999px;padding:.4rem .7rem}}main{{max-width:1200px;margin:auto;padding:2rem 32px 5rem;display:grid;grid-template-columns:220px minmax(0,640px) 280px;gap:40px}}nav{{border-right:1px solid var(--line)}}nav ol{{padding-left:1.2rem;line-height:2.2}}nav li.current{{font-weight:800}}.mobile-progress{{display:none}}section{{min-width:0}}h1{{font:800 clamp(2rem,5vw,3.6rem)/1 Archivo,Arial,sans-serif;letter-spacing:-.04em;margin:.25rem 0 1rem}}h2{{font:800 1.5rem Archivo,Arial,sans-serif}}p{{line-height:1.55;max-width:62ch}}.status{{border-left:4px solid var(--link);padding:.7rem 1rem;background:white;margin:1.5rem 0}}.fields{{display:grid;grid-template-columns:1fr 1fr;gap:1rem}}label{{display:grid;gap:.45rem;font-weight:700}}input,select{{min-height:48px;border:1px solid #718089;border-radius:8px;padding:.7rem;background:white;color:var(--ink)}}.actions{{display:flex;gap:.75rem;margin-top:1.5rem;flex-wrap:wrap}}aside{{position:sticky;top:24px;height:max-content;border:1px solid var(--line);border-radius:12px;background:white;padding:1.25rem}}dl{{margin:0}}dt{{color:var(--muted);font-size:.8rem;margin-top:.8rem}}dd{{margin:.2rem 0;font-weight:700}}.receipt{{border-top:3px solid var(--ink);padding-top:1rem}}.evidence{{display:grid;grid-template-columns:1fr auto;gap:.3rem 1rem;padding:.9rem 0;border-top:1px solid var(--line)}}.source{{font:600 .75rem 'IBM Plex Mono',monospace;color:var(--muted)}}.hidden{{display:none!important}}.error{{border-left-color:var(--bad)}}.invite{{margin-top:1.5rem;border:1px solid var(--line);border-radius:12px;padding:1rem;background:white}}small{{color:var(--muted)}}
@media(max-width:900px){{main{{grid-template-columns:176px minmax(0,1fr);padding:1.5rem 24px}}aside{{position:static;grid-column:2}}}}
@media(max-width:600px){{header{{align-items:flex-start;gap:.75rem;flex-direction:column}}main{{display:block;padding:1.25rem 16px 6rem}}nav ol{{display:none}}nav{{border:0}}.mobile-progress{{display:block;color:var(--muted);margin-bottom:1rem}}.fields{{grid-template-columns:1fr}}.actions{{display:grid}}aside{{margin-top:1.5rem}}button{{width:100%}}}}
@media(prefers-reduced-motion:no-preference){{section{{animation:enter .16s ease-out}}@keyframes enter{{from{{opacity:.6;transform:translateY(4px)}}}}}}
</style></head><body>
<header><span class="brand">BackBond</span><span class="mode">Demo · not activated</span></header>
<main><nav aria-label="Task progress"><span id="mobile-progress" class="mobile-progress">Step 1 of 6 · Offer</span><ol><li class="current">Offer</li><li>Choose agent</li><li>Describe task</li><li>Approve</li><li>Status</li><li>Private receipt</li></ol></nav>
<section aria-labelledby="step-title"><small id="step-count">Step 1 of 6</small><h1 id="step-title" tabindex="-1">Give your agent one clear task.</h1><p id="body-copy">Approve the details, follow what happened, and keep a private receipt.</p><p id="protection-banner" class="status" role="note">{protection}</p><div id="status" class="status" role="status" aria-live="polite"><strong>Supported synthetic agent</strong><br>BackBond can observe proposals and simulated results in this demo. Passive Monitor observation does not authorize an action.</div>
<div id="agent-selection" class="hidden"><label>Supported agent<select id="supported-agent"><option value="synthetic-purchase-agent">Synthetic purchase agent · demo</option></select></label><p><small>Only the listed synthetic agent is supported in Stage A. No live account is connected.</small></p></div>
<div id="form" class="fields hidden"><label>Item or service<input id="item" value="Desk lamp" required aria-describedby="item-help"><small id="item-help">Required for this mandate.</small></label><label>Merchant<input id="merchant" value="Synthetic merchant" required></label><label>Quantity<input id="quantity" value="1" type="number" min="1" required></label><label>Maximum total<input id="maximum" value="$48.00" required></label><label>Substitution rule<select id="substitution"><option>Ask me</option><option>None</option><option>Exact attributes</option></select></label><label>Note (optional)<input value="Warm white bulb"></label></div>
<div class="actions"><button id="primary">Choose an agent</button><button id="deny-approval" class="secondary hidden" type="button">Deny and cancel</button><button id="fixture-button" class="secondary">Open a fixture</button><select id="fixture" aria-label="Direct fixture"></select></div>
<div id="receipt" class="hidden receipt" tabindex="-1"><small>Simulated receipt</small><h2 id="outcome"></h2><div id="evidence"></div><div class="invite"><h2>Optional invitation</h2><p>Share only a generic completion note. Your identity, order, merchant, amount, mandate, evidence, and receipt reference stay private.</p><dl id="invite" class="hidden"></dl><p id="invite-state" role="status" aria-live="polite">No invitation created.</p><div class="actions"><button id="create-invitation" disabled>Create invitation</button><button id="keep-private" class="secondary">Keep private</button></div></div></div></section>
<aside aria-label="Immutable mandate summary"><strong>Mandate summary · v1</strong><dl><dt>Item</dt><dd id="summary-item">Desk lamp</dd><dt>Merchant</dt><dd>Synthetic merchant</dd><dt>Limit</dt><dd>$48.00</dd><dt>Approval</dt><dd id="approval">Not approved</dd></dl><p><small>Approval applies only to this version. Editing creates a new version.</small></p></aside></main>
<script>
const cases={cases};const fixture=document.querySelector('#fixture');cases.forEach(x=>fixture.add(new Option(x.replaceAll('-',' '),x)));
const titles=['Give your agent one clear task.','Choose a supported agent.','Describe the task.','Approve this task','Agent is working','Your private receipt'];const stepNames=['Offer','Choose agent','Describe task','Approve','Status','Private receipt'];const primaryLabels=['Choose an agent','Continue','Review task','Approve task','View private receipt','Done'];const statusByStep=[['Supported synthetic agent','BackBond can observe proposals and simulated results in this demo. Passive Monitor observation does not authorize an action.'],['Choose before continuing','Select one supported demo agent.'],['Task details','These details become mandate v1 when you approve them.'],['Human approval required','Approve this exact mandate or deny and cancel it.'],['Approved · simulated run','The selected demo agent is producing a simulated result.'],['Task finished','Review the private simulated receipt before choosing what to do next.']];let step=0;let currentResult=null;let approvalDenied=false;
function renderState(){{const text=`Step ${{step+1}} of 6`;document.querySelector('#step-count').textContent=text;document.querySelector('#mobile-progress').textContent=`${{text}} · ${{stepNames[step]}}`;document.querySelectorAll('nav li').forEach((n,i)=>n.classList.toggle('current',i===step));document.querySelector('#step-title').textContent=titles[step];document.querySelector('#primary').textContent=approvalDenied?'Start over':primaryLabels[step];document.querySelector('#agent-selection').classList.toggle('hidden',step!==1);document.querySelector('#form').classList.toggle('hidden',step!==2);document.querySelector('#deny-approval').classList.toggle('hidden',step!==3||approvalDenied);if(!currentResult){{const copy=approvalDenied?['Approval denied','The task was cancelled and did not run.']:statusByStep[step];document.querySelector('#status').classList.toggle('error',approvalDenied);document.querySelector('#status').innerHTML=`<strong>${{copy[0]}}</strong><br>${{copy[1]}}`;}}}}
function focusTitle(){{document.querySelector('#step-title').focus()}}function resetFlow(){{step=0;currentResult=null;approvalDenied=false;document.querySelector('#receipt').classList.add('hidden');document.querySelector('#approval').textContent='Not approved';renderState();focusTitle()}}function denyApproval(){{approvalDenied=true;currentResult=null;document.querySelector('#approval').textContent='Denied · task cancelled';renderState();document.querySelector('#status').focus()}}function next(){{if(approvalDenied||step===5){{resetFlow();return}}step+=1;if(step===3)document.querySelector('#approval').textContent='Awaiting your approval';if(step===4)document.querySelector('#approval').textContent='Approved for mandate v1';renderState();if(step===5)loadReceipt('correct');focusTitle()}}
async function loadReceipt(name){{const r=await fetch('/api/receipt?case='+encodeURIComponent(name));const d=await r.json();currentResult=d;document.querySelector('#receipt').classList.remove('hidden');document.querySelector('#outcome').textContent=d.title;document.querySelector('#evidence').innerHTML=d.evidence.map(e=>`<div class="evidence"><span><strong>${{e.label}}</strong><br>${{e.value}}</span><span class="source">${{e.source}}</span></div>`).join('');document.querySelector('#invite').classList.add('hidden');document.querySelector('#invite').innerHTML='';document.querySelector('#invite-state').textContent=d.status==='MATCH'?'No invitation created.':'Invitation unavailable for this result.';document.querySelector('#create-invitation').disabled=d.status!=='MATCH';document.querySelector('#status').classList.toggle('error',['ERROR','DENIED','EXPIRED','DISCONNECTED','UNSUPPORTED','MISMATCH'].includes(d.status));document.querySelector('#status').innerHTML=`<strong>${{d.title}}</strong><br>Fixture: ${{name.replaceAll('-',' ')}}`;document.querySelector('#receipt').focus()}}
function createInvitation(){{if(!currentResult||currentResult.status!=='MATCH')return;const invitation=document.querySelector('#invite');invitation.innerHTML=Object.entries(currentResult.invitation).map(([k,v])=>`<dt>${{k.replaceAll('_',' ')}}</dt><dd>${{v}}</dd>`).join('');invitation.classList.remove('hidden');document.querySelector('#invite-state').textContent='Invitation created. Only the fields shown above will be shared.';document.querySelector('#create-invitation').disabled=true;document.querySelector('#keep-private').focus()}}function keepPrivate(){{document.querySelector('#invite').classList.add('hidden');document.querySelector('#invite').innerHTML='';document.querySelector('#invite-state').textContent='Kept private. No invitation was created or shared.';document.querySelector('#create-invitation').disabled=!currentResult||currentResult.status!=='MATCH';document.querySelector('#primary').focus()}}
document.querySelector('#primary').addEventListener('click',next);document.querySelector('#deny-approval').addEventListener('click',denyApproval);document.querySelector('#fixture-button').addEventListener('click',()=>loadReceipt(fixture.value));document.querySelector('#create-invitation').addEventListener('click',createInvitation);document.querySelector('#keep-private').addEventListener('click',keepPrivate);renderState();
</script></body></html>"""


class DemoHandler(BaseHTTPRequestHandler):
    db_path: str | Path

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/receipt":
            case = parse_qs(parsed.query).get("case", ["correct"])[0]
            body = json.dumps(receipt_fixture(case, self.db_path)).encode()
            content_type = "application/json; charset=utf-8"
        elif parsed.path == "/":
            body = render_page().encode()
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def make_handler(db_path: str | Path) -> type[DemoHandler]:
    class ConfiguredDemoHandler(DemoHandler):
        pass

    ConfiguredDemoHandler.db_path = db_path
    return ConfiguredDemoHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the private Stage A demo UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--db", default="stage-a-demo-ui.sqlite3")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.db))
    print(f"Stage A demo: http://{args.host}:{args.port} (database: {args.db})")
    server.serve_forever()


if __name__ == "__main__":
    main()

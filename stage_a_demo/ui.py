from __future__ import annotations

import argparse
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .activation import (
    default_program_version,
    default_registry,
    referral_disclosure,
    surface_protection_state,
)
from .approval import approve_mandate, build_issuer
from .core import (
    MandateExceeded,
    APPROVAL_ORIGINS,
    EvidenceStore,
    Mandate,
    SyntheticMerchantAdapter,
)


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
    "over-mandate": ("REFUSED", "That is outside what you approved, so it did not run.", "Missing"),
    "error": ("ERROR", "We could not complete that step.", "Missing"),
}

BACKEND_CASES = {
    "correct": ("SKU-LAMP", 4800, "SKU-LAMP", 4800, True, "idem-correct"),
    "incorrect": ("SKU-LAMP", 4800, "SKU-LAMP", 4700, True, "idem-incorrect"),
    # Outside the mandate: refused before the merchant is reached (A4).
    "over-mandate": ("SKU-LAMP", 4800, "SKU-LAMP-PREMIUM", 99_900, True, "idem-over-mandate"),
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


def _completion_date(completed_at: str | None) -> str:
    """Render the recorded completion instant. No execution, no date."""
    if not completed_at:
        return "Not recorded"
    try:
        stamp = time.strptime(completed_at, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return "Not recorded"
    return time.strftime("%d %b %Y", stamp)


def invitation_payload(completed_at: str | None = None) -> dict[str, str]:
    # BEAAA-35453 A14: `completed` is read from the execution that produced the
    # result, never from a constant. A case that did not execute has no date,
    # and the gate below refuses to build a note for it at all.
    return {
        "brand": "BackBond",
        "mode": "Demo · not activated",
        "category": "Household purchase",
        "completed": _completion_date(completed_at),
        "invitation_code": "DEMO-7F2K",
    }


def invitation_transition(
    result_status: str, action: str, completed_at: str | None = None
) -> dict[str, object]:
    # Referral attribution never implies coverage: the referral surface
    # states protection only from the activation gate.
    disclosure = protection_statement("referral")
    if action == "create":
        if result_status not in VERIFIED_INVITATION_STATUSES:
            return {"state": "unavailable", "payload": None, "protection": disclosure}
        return {
            "state": "created",
            "payload": invitation_payload(completed_at),
            "protection": disclosure,
        }
    if action == "keep_private":
        return {"state": "kept_private", "payload": None, "protection": disclosure}
    if action == "done":
        return {"state": "reset", "payload": None, "protection": disclosure}
    raise ValueError(f"unsupported invitation action: {action}")


class ApprovalRequired(Exception):
    """A write-shaped step was reached without a human approval grant."""


class CaseNotExecuted(Exception):
    """A read-shaped request asked for a case that no approved run produced."""

    def __init__(self, case: str) -> None:
        super().__init__(f"{case} has no recorded execution; approve the mandate first")
        self.case = case


def _mandate_for(case: str) -> Mandate:
    mandate_sku, mandate_amount = BACKEND_CASES[case][0], BACKEND_CASES[case][1]
    return Mandate(f"mandate-{case}-v1", mandate_sku, mandate_amount)


def approve_case(
    case: str,
    db_path: str | Path,
    origin: str = "approval-step",
    issuer: object | None = None,
) -> dict[str, object]:
    """The human approval step, and the only place a signing capability appears.

    The issuer is built once at start-up from an injected secret and handed in;
    this function does not construct one, and nothing on the read or execution
    path can reach it. The server therefore cannot mint the approval evidence
    the execution path later requires. BEAAA-34389 defect 1.
    """
    if issuer is None:
        raise ApprovalRequired("no human approval capability is wired into this server")
    if case not in BACKEND_CASES:
        raise ApprovalRequired(f"{case} is a display-only fixture and cannot be approved")
    if origin not in APPROVAL_ORIGINS:
        raise ApprovalRequired(f"unknown approval origin: {origin}")
    mandate = _mandate_for(case)
    store = EvidenceStore(db_path)
    try:
        grant_id = approve_mandate(
            store, case_id=case, mandate=mandate, issuer=issuer, origin=origin
        )
    except PermissionError as exc:
        raise ApprovalRequired(str(exc)) from exc
    finally:
        store.close()
    return {"grant": grant_id, "case": case, "mandate_version": "v1", "origin": origin}


def execute_case(case: str, db_path: str | Path, grant: str | None) -> dict[str, object]:
    """Run one approved case.

    The execution path holds no verifier, no token and no signing capability.
    It hands the store a grant id; the store consumes the approval, re-checks
    its origin and rebuilds the verifier itself. BEAAA-35331.
    """
    if case not in BACKEND_CASES:
        return receipt_fixture(case, db_path)
    if not grant:
        raise ApprovalRequired(f"{case} needs an approval grant before it can execute")
    store = EvidenceStore(db_path)
    try:
        _, _, asserted_sku, asserted_amount, has_evidence, idem = BACKEND_CASES[case]
        kwargs = dict(
            case_id=case,
            grant_id=grant,
            asserted_sku=asserted_sku,
            asserted_amount_cents=asserted_amount,
            idempotency_key=idem,
            merchant=SyntheticMerchantAdapter(),
            independent_evidence=has_evidence,
        )
        try:
            store.record_case(**kwargs)
        except MandateExceeded as refusal:
            # Capability path: refused before the grant was spent and before the
            # merchant was called. Nothing executed, so there is no packet.
            return {
                "mode": "Demo · not activated",
                "protection": protection_block("receipt"),
                "receipt_label": "Nothing ran",
                "case": case,
                "status": "REFUSED",
                "title": FIXTURES[case][1],
                "refusal": str(refusal),
                "evidence": [
                    {"label": "Human approval", "value": "Still unspent", "source": "Verified event"},
                    {"label": "Agent result", "value": "Refused before execution", "source": "Agent reported"},
                    {"label": "Merchant result", "value": "Not contacted", "source": "Missing"},
                ],
                # BEAAA-35453 A13/A14: nothing executed, so there is nothing to
                # share and no completion to name. The invitation is built only
                # by /api/invitation, and only when the user asks for one.
                "invitation": None,
                "completed_at": None,
                "backend_packet": None,
                "authoritative_executions": 0,
            }
        except PermissionError as exc:
            raise ApprovalRequired(str(exc)) from exc
        if case == "duplicate":
            # The duplicate fixture retries the request under the same key, so
            # the store answers it from the execution it already recorded
            # (BEAAA-35354 A6). A grant that is genuinely spent is still
            # refused, which the except below still catches. Either way the
            # merchant is reached once.
            try:
                store.record_case(**kwargs)
            except PermissionError:
                pass
    finally:
        store.close()
    return receipt_fixture(case, db_path)


def receipt_fixture(case: str, db_path: str | Path) -> dict[str, object]:
    """Read one receipt. This path never approves, executes, or writes."""
    status, title, provenance = FIXTURES.get(case, FIXTURES["error"])
    packet = None
    authoritative_executions = None
    approved = False
    completed_at = None
    if case in BACKEND_CASES:
        idem = BACKEND_CASES[case][5]
        store = EvidenceStore(db_path)
        try:
            packet = store.packet_or_none(case)
            if packet is None:
                raise CaseNotExecuted(case)
            authoritative_executions = store.execution_count(idem)
        finally:
            store.close()
        status = str(packet["status"])
        title = FIXTURES[case][1]
        provenance = "Missing" if packet["sources"]["independent_merchant_evidence"] is None else "Verified event"
        approved = packet["sources"]["approval"].get("source") == "human-approval"
        completed_at = _recorded_completion(packet)
    return {
        "mode": "Demo · not activated",
        "protection": protection_block("receipt"),
        "receipt_label": "Simulated receipt",
        "case": case,
        "status": status,
        "title": title,
        "approved": approved,
        "mandate": {"version": "v1", "item": "Desk lamp", "merchant": "Synthetic merchant", "quantity": 1, "maximum": "$48.00", "substitutions": "Ask me"},
        "evidence": [
            {"label": "Human approval", "value": "Approved for mandate v1", "source": "Verified event"}
            if approved
            else {"label": "Human approval", "value": "No approval recorded", "source": "Missing"},
            {"label": "Agent result", "value": "One simulated item", "source": "Agent reported"},
            {"label": "Merchant result", "value": "Available" if provenance != "Missing" else "Not received", "source": provenance},
        ],
        # BEAAA-35453 A13: reading a receipt is not asking to share it. The
        # shareable artifact is built only by /api/invitation, and only for the
        # action the user chose. `completed_at` is the recorded execution
        # instant the note would name, or None when nothing executed.
        "invitation": None,
        "completed_at": completed_at,
        "backend_packet": packet,
        "authoritative_executions": authoritative_executions,
    }


def _recorded_completion(packet: dict[str, object]) -> str | None:
    """The instant the execution that produced this packet was recorded."""
    execution = packet.get("execution") or {}
    if not isinstance(execution, dict):
        return None
    try:
        recorded = json.loads(str(execution.get("receipt_json") or "{}"))
    except ValueError:
        return None
    value = recorded.get("completed_at") if isinstance(recorded, dict) else None
    return str(value) if value else None


def invitation_decision(case: str, action: str, db_path: str | Path) -> dict[str, object]:
    """Carry the user's sharing choice to the server. BEAAA-35453 A13/A14.

    The status the gate judges is read from what this server recorded, never
    taken from the request, so a caller cannot ask for a note by asserting its
    own result. A case with no recorded execution raises
    :class:`CaseNotExecuted` before any note is considered.
    """
    result = receipt_fixture(case, db_path)
    completed_at = result.get("completed_at")
    return invitation_transition(
        str(result["status"]), action, str(completed_at) if completed_at else None
    )


def render_page() -> str:
    cases = json.dumps(list(FIXTURES))
    backend_cases = json.dumps(list(BACKEND_CASES))
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
const cases={cases};const backendCases={backend_cases};let approvalGrant=null;const fixture=document.querySelector('#fixture');cases.forEach(x=>fixture.add(new Option(x.replaceAll('-',' '),x)));
const titles=['Give your agent one clear task.','Choose a supported agent.','Describe the task.','Approve this task','Agent is working','Your private receipt'];const stepNames=['Offer','Choose agent','Describe task','Approve','Status','Private receipt'];const primaryLabels=['Choose an agent','Continue','Review task','Approve task','View private receipt','Done'];const statusByStep=[['Supported synthetic agent','BackBond can observe proposals and simulated results in this demo. Passive Monitor observation does not authorize an action.'],['Choose before continuing','Select one supported demo agent.'],['Task details','These details become mandate v1 when you approve them.'],['Human approval required','Approve this exact mandate or deny and cancel it.'],['Approved · simulated run','The selected demo agent is producing a simulated result.'],['Task finished','Review the private simulated receipt before choosing what to do next.']];let step=0;let currentResult=null;let approvalDenied=false;
function renderState(){{const text=`Step ${{step+1}} of 6`;document.querySelector('#step-count').textContent=text;document.querySelector('#mobile-progress').textContent=`${{text}} · ${{stepNames[step]}}`;document.querySelectorAll('nav li').forEach((n,i)=>n.classList.toggle('current',i===step));document.querySelector('#step-title').textContent=titles[step];document.querySelector('#primary').textContent=approvalDenied?'Start over':primaryLabels[step];document.querySelector('#agent-selection').classList.toggle('hidden',step!==1);document.querySelector('#form').classList.toggle('hidden',step!==2);document.querySelector('#deny-approval').classList.toggle('hidden',step!==3||approvalDenied);if(!currentResult){{const copy=approvalDenied?['Approval denied','The task was cancelled and did not run.']:statusByStep[step];document.querySelector('#status').classList.toggle('error',approvalDenied);document.querySelector('#status').innerHTML=`<strong>${{copy[0]}}</strong><br>${{copy[1]}}`;}}}}
function focusTitle(){{document.querySelector('#step-title').focus()}}function resetFlow(){{step=0;currentResult=null;approvalDenied=false;document.querySelector('#receipt').classList.add('hidden');document.querySelector('#approval').textContent='Not approved';renderState();focusTitle()}}function denyApproval(){{approvalDenied=true;currentResult=null;document.querySelector('#approval').textContent='Denied · task cancelled';renderState();document.querySelector('#status').focus()}}function next(){{if(approvalDenied||step===5){{resetFlow();return}}step+=1;if(step===3)document.querySelector('#approval').textContent='Awaiting your approval';if(step===4)approveMandate('correct','approval-step').then(g=>{{approvalGrant=g;document.querySelector('#approval').textContent=g?'Approved for mandate v1':'Approval failed · nothing ran'}});renderState();if(step===5)loadReceipt('correct');focusTitle()}}
async function approveMandate(name,origin){{const r=await fetch('/api/approve',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{case:name,origin:origin}})}});if(!r.ok)return null;return (await r.json()).grant}}
async function loadReceipt(name){{let r;if(backendCases.includes(name)){{const grant=approvalGrant||await approveMandate(name,'fixture-explorer');approvalGrant=null;if(!grant){{document.querySelector('#status').classList.add('error');document.querySelector('#status').innerHTML='<strong>Not approved</strong><br>Nothing ran and nothing was recorded.';return}}r=await fetch('/api/receipt',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{case:name,grant:grant}})}});}}else{{r=await fetch('/api/receipt?case='+encodeURIComponent(name));}}const d=await r.json();if(!r.ok){{document.querySelector('#status').classList.add('error');document.querySelector('#status').innerHTML='<strong>Not approved</strong><br>Nothing ran and nothing was recorded.';return}}currentResult=d;document.querySelector('#receipt').classList.remove('hidden');document.querySelector('#outcome').textContent=d.title;document.querySelector('#evidence').innerHTML=d.evidence.map(e=>`<div class="evidence"><span><strong>${{e.label}}</strong><br>${{e.value}}</span><span class="source">${{e.source}}</span></div>`).join('');document.querySelector('#invite').classList.add('hidden');document.querySelector('#invite').innerHTML='';document.querySelector('#invite-state').textContent=d.status==='MATCH'?'No invitation created.':'Invitation unavailable for this result.';document.querySelector('#create-invitation').disabled=d.status!=='MATCH';document.querySelector('#status').classList.toggle('error',['ERROR','DENIED','EXPIRED','DISCONNECTED','UNSUPPORTED','MISMATCH'].includes(d.status));document.querySelector('#status').innerHTML=`<strong>${{d.title}}</strong><br>Fixture: ${{name.replaceAll('-',' ')}}`;document.querySelector('#receipt').focus()}}
const inviteStates={{created:'Invitation created. Only the fields shown above will be shared.',kept_private:'Kept private. No invitation was created or shared.',unavailable:'Invitation unavailable for this result.',reset:'No invitation created.'}};
async function decideInvitation(action){{if(!currentResult)return null;const r=await fetch('/api/invitation',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{case:currentResult.case,action:action}})}});if(!r.ok)return {{state:'unavailable',payload:null}};return await r.json()}}
function renderInvitation(d){{const invitation=document.querySelector('#invite');const payload=d&&d.payload;invitation.innerHTML=payload?Object.entries(payload).map(([k,v])=>`<dt>${{k.replaceAll('_',' ')}}</dt><dd>${{v}}</dd>`).join(''):'';invitation.classList.toggle('hidden',!payload);document.querySelector('#invite-state').textContent=inviteStates[(d&&d.state)||'unavailable'];document.querySelector('#create-invitation').disabled=!!payload}}
async function createInvitation(){{renderInvitation(await decideInvitation('create'));document.querySelector('#keep-private').focus()}}
async function keepPrivate(){{renderInvitation(await decideInvitation('keep_private'));document.querySelector('#primary').focus()}}
document.querySelector('#primary').addEventListener('click',next);document.querySelector('#deny-approval').addEventListener('click',denyApproval);document.querySelector('#fixture-button').addEventListener('click',()=>loadReceipt(fixture.value));document.querySelector('#create-invitation').addEventListener('click',createInvitation);document.querySelector('#keep-private').addEventListener('click',keepPrivate);renderState();
</script></body></html>"""


class DemoHandler(BaseHTTPRequestHandler):
    db_path: str | Path
    # Built once at start-up. Reachable only from the /api/approve route below.
    approval_issuer: object | None = None

    def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode())
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/receipt":
                # Read-only: no approval is minted and no case is recorded here.
                case = parse_qs(parsed.query).get("case", ["correct"])[0]
                self._send_json(receipt_fixture(case, self.db_path))
                return
            if parsed.path != "/":
                self.send_error(404)
                return
            body = render_page().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except CaseNotExecuted as exc:
            self._send_json({"error": "approval_required", "case": exc.case, "detail": str(exc)}, 409)
        except Exception as exc:  # never drop the connection on the caller
            self._send_json({"error": "internal_error", "detail": str(exc)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        case = ""
        try:
            payload = self._read_json()
            case = str(payload.get("case", ""))
            if parsed.path == "/api/approve":
                self._send_json(
                    approve_case(
                        case,
                        self.db_path,
                        str(payload.get("origin", "approval-step")),
                        issuer=self.approval_issuer,
                    )
                )
                return
            if parsed.path == "/api/receipt":
                grant = payload.get("grant")
                self._send_json(execute_case(case, self.db_path, str(grant) if grant else None))
                return
            if parsed.path == "/api/invitation":
                # The sharing choice, carried to the server. Read-only: it
                # approves nothing, executes nothing and records nothing.
                action = str(payload.get("action", ""))
                try:
                    self._send_json(
                        invitation_decision(case or "correct", action, self.db_path)
                    )
                except ValueError as exc:
                    self._send_json({"error": "unsupported_action", "detail": str(exc)}, 400)
                return
            self.send_error(404)
        except ApprovalRequired as exc:
            self._send_json({"error": "approval_required", "case": case, "detail": str(exc)}, 403)
        except CaseNotExecuted as exc:
            self._send_json({"error": "approval_required", "case": exc.case, "detail": str(exc)}, 409)
        except Exception as exc:  # never drop the connection on the caller
            self._send_json({"error": "internal_error", "detail": str(exc)}, 500)

    def log_message(self, format: str, *args: object) -> None:
        return


def make_handler(db_path: str | Path, issuer: object | None = None) -> type[DemoHandler]:
    class ConfiguredDemoHandler(DemoHandler):
        pass

    ConfiguredDemoHandler.approval_issuer = issuer

    ConfiguredDemoHandler.db_path = db_path
    return ConfiguredDemoHandler


def build_server(host: str, port: int, db_path: str | Path) -> ThreadingHTTPServer:
    """Single wiring path shared by main() and the tests.

    The signing capability is built here, at start-up, from a secret injected
    from outside the execution path, and is reachable only from /api/approve.
    """
    return ThreadingHTTPServer((host, port), make_handler(db_path, build_issuer()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the private Stage A demo UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--db", default="stage-a-demo-ui.sqlite3")
    args = parser.parse_args()
    server = build_server(args.host, args.port, args.db)
    print(f"Stage A demo: http://{args.host}:{args.port} (database: {args.db})")
    server.serve_forever()


if __name__ == "__main__":
    main()

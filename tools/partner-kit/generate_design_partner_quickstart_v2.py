"""Generator for the BackBond Design Partner Quick Start (v2).

Part of the BackBond scanner solution's partner kit. Renders the seven-page,
partner-facing Quick Start PDF that accompanies the design-partner connection
pack, using the shared BackBond brand toolkit (fonts, colours, step ribbon,
callouts, tables). Run: python3 generate_design_partner_quickstart_v2.py
"""
from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)


OUT_DIR = Path(__file__).resolve().parent / "output"
OUT_FILE = OUT_DIR / "BackBond_Design_Partner_Scanner_Quick_Start_v2.pdf"
METADATA_FILE = OUT_DIR / "BackBond_Design_Partner_Scanner_Quick_Start_v2.metadata.json"
BRAND_VERSION = "v1.6"
BRAND_SHA256 = "a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090"

# Stable PDF timestamps/document IDs make the reviewed artifact checksum replayable.
rl_config.invariant = True

PAGE_W, PAGE_H = A4
LEFT = 17 * mm
RIGHT = 17 * mm
TOP = 19 * mm
BOTTOM = 17 * mm
CONTENT_W = PAGE_W - LEFT - RIGHT

NAVY = colors.HexColor("#0A1931")
BLUE = colors.HexColor("#246BFD")
CYAN = colors.HexColor("#24C8D8")
INK = colors.HexColor("#162033")
MUTED = colors.HexColor("#5F6B7A")
PALE = colors.HexColor("#F3F7FC")
PALE_BLUE = colors.HexColor("#EAF1FF")
PALE_GREEN = colors.HexColor("#EAF8F2")
GREEN = colors.HexColor("#147A55")
PALE_AMBER = colors.HexColor("#FFF7E7")
AMBER = colors.HexColor("#A35B00")
LINE = colors.HexColor("#D8E1EC")
WHITE = colors.white
CODE_BG = colors.HexColor("#0E172A")
CODE_FG = colors.HexColor("#E8EEF8")

FOOTER = "Confidential — authorized design partner pilot — v2, 2026-07-21"


def find_font(names: list[str]) -> str | None:
    roots = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")]
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.exists():
                return str(candidate)
    return None


regular_font = find_font(["aptos.ttf", "arial.ttf", "DejaVuSans.ttf"])
bold_font = find_font(["aptos-bold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"])
mono_font = find_font(["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"])

if regular_font and bold_font:
    pdfmetrics.registerFont(TTFont("BBRegular", regular_font))
    pdfmetrics.registerFont(TTFont("BBBold", bold_font))
    BODY_FONT = "BBRegular"
    BOLD_FONT = "BBBold"
else:
    BODY_FONT = "Helvetica"
    BOLD_FONT = "Helvetica-Bold"

if mono_font:
    pdfmetrics.registerFont(TTFont("BBMono", mono_font))
    MONO_FONT = "BBMono"
else:
    MONO_FONT = "Courier"


styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="BBBody", parent=styles["BodyText"], fontName=BODY_FONT,
                          fontSize=8.6, leading=11.4, textColor=INK, spaceAfter=3))
styles.add(ParagraphStyle(name="BBSmall", parent=styles["BBBody"], fontSize=7.2,
                          leading=9.2, textColor=MUTED, spaceAfter=3))
styles.add(ParagraphStyle(name="BBH1", parent=styles["Heading1"], fontName=BOLD_FONT,
                          fontSize=17, leading=19, textColor=NAVY, spaceAfter=3))
styles.add(ParagraphStyle(name="BBH2", parent=styles["Heading2"], fontName=BOLD_FONT,
                          fontSize=12, leading=14.5, textColor=NAVY, spaceBefore=2, spaceAfter=4))
styles.add(ParagraphStyle(name="BBH3", parent=styles["Heading3"], fontName=BOLD_FONT,
                          fontSize=10.2, leading=12.5, textColor=INK, spaceBefore=4, spaceAfter=4))
styles.add(ParagraphStyle(name="BBLabel", parent=styles["BBBody"], fontName=BOLD_FONT,
                          fontSize=7.2, leading=9, textColor=BLUE, spaceAfter=4))
styles.add(ParagraphStyle(name="BBCoverTitle", parent=styles["BBH1"], fontSize=29,
                          leading=33, textColor=WHITE, spaceAfter=12))
styles.add(ParagraphStyle(name="BBCoverSub", parent=styles["BBBody"], fontSize=12.5,
                          leading=17, textColor=colors.HexColor("#DCE8FF"), spaceAfter=8))
styles.add(ParagraphStyle(name="BBBullet", parent=styles["BBBody"], leftIndent=10,
                          firstLineIndent=-7, bulletIndent=0, spaceAfter=3))
styles.add(ParagraphStyle(name="BBCode", parent=styles["Code"], fontName=MONO_FONT,
                          fontSize=6.1, leading=7.7, textColor=CODE_FG, leftIndent=0,
                          rightIndent=0, spaceAfter=0))


def p(text: str, style: str = "BBBody") -> Paragraph:
    return Paragraph(text, styles[style])


def bullet(text: str) -> Paragraph:
    return Paragraph(text, styles["BBBullet"], bulletText="•")


def link(label: str, url: str) -> str:
    return f'<link href="{escape(url)}" color="#246BFD"><u>{escape(label)}</u></link>'


def section(label: str, title: str, intro: str | None = None) -> list[Flowable]:
    flow: list[Flowable] = [p(label.upper(), "BBLabel"), p(title, "BBH1")]
    if intro:
        flow.append(p(intro))
    return flow


def code_block(code: str, title: str | None = None) -> Flowable:
    rows: list[list[Flowable]] = []
    if title:
        rows.append([p(escape(title), "BBSmall")])
    rows.append([Preformatted(code.strip("\n"), styles["BBCode"], maxLineLength=118)])
    table = Table(rows, colWidths=[CONTENT_W - 10 * mm], hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#26334C")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5 * mm),
    ]
    if title:
        style.extend([
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#AFC2DF")),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 1.5 * mm),
            ("TOPPADDING", (0, 0), (-1, 0), 2.5 * mm),
        ])
    table.setStyle(TableStyle(style))
    return table


def callout(title: str, body: str, kind: str = "blue") -> Flowable:
    palettes = {"blue": (PALE_BLUE, BLUE), "green": (PALE_GREEN, GREEN), "amber": (PALE_AMBER, AMBER)}
    bg, accent = palettes[kind]
    t = Table(
        [[p(f'<font color="{accent.hexval()}"><b>{escape(title)}</b></font><br/>{body}', "BBBody")]],
        colWidths=[CONTENT_W], hAlign="LEFT",
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor(accent.hexval())),
        ("LINEBEFORE", (0, 0), (0, -1), 3.2, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
    ]))
    return t


def data_table(headers: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    data: list[list[Paragraph]] = [[p(f"<b>{escape(h)}</b>", "BBSmall") for h in headers]]
    for row in rows:
        data.append([p(cell, "BBSmall") for cell in row])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("GRID", (0, 0), (-1, -1), 0.45, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
    ]
    for idx in range(1, len(data)):
        if idx % 2 == 0:
            style.append(("BACKGROUND", (0, idx), (-1, idx), PALE))
    t.setStyle(TableStyle(style))
    return t


class StepFlow(Flowable):
    """Six-step ribbon. Each step is (header, label, kind) with kind in
    {you, us, together} → blue / green / amber, matching the legend."""

    COLORS = {"you": (PALE_BLUE, BLUE), "us": (PALE_GREEN, GREEN), "together": (PALE_AMBER, AMBER)}

    def __init__(self, steps: list[tuple[str, str, str]], width: float, height: float = 29 * mm):
        super().__init__()
        self.steps = steps
        self.width = width
        self.height = height

    def wrap(self, availWidth, availHeight):
        return min(self.width, availWidth), self.height

    def draw(self):
        c = self.canv
        n = len(self.steps)
        gap = 2.8 * mm
        box_w = (self.width - gap * (n - 1)) / n
        box_h = 21 * mm
        y = 5 * mm
        for i, (header, label, kind) in enumerate(self.steps):
            fill, stroke = self.COLORS[kind]
            x = i * (box_w + gap)
            c.setFillColor(fill)
            c.setStrokeColor(stroke)
            c.roundRect(x, y, box_w, box_h, 2.2 * mm, fill=1, stroke=1)
            c.setFillColor(stroke)
            c.setFont(BOLD_FONT, 7.5)
            c.drawString(x + 3 * mm, y + box_h - 5 * mm, header)
            text = c.beginText(x + 3 * mm, y + box_h - 10 * mm)
            text.setFont(BODY_FONT, 6.7)
            text.setFillColor(INK)
            line = ""
            for word in label.split():
                trial = (line + " " + word).strip()
                if c.stringWidth(trial, BODY_FONT, 6.7) > box_w - 6 * mm:
                    text.textLine(line)
                    line = word
                else:
                    line = trial
            if line:
                text.textLine(line)
            c.drawText(text)
            if i < n - 1:
                c.setStrokeColor(MUTED)
                c.setLineWidth(0.8)
                x2 = x + box_w + gap - 0.8 * mm
                yy = y + box_h / 2
                c.line(x + box_w + 0.8 * mm, yy, x2, yy)
                c.line(x2 - 1.5 * mm, yy + 1 * mm, x2, yy)
                c.line(x2 - 1.5 * mm, yy - 1 * mm, x2, yy)


def page_header_footer(canvas, doc):
    canvas.saveState()
    if doc.page > 1:
        canvas.setFillColor(NAVY)
        canvas.rect(0, PAGE_H - 13 * mm, PAGE_W, 13 * mm, fill=1, stroke=0)
        canvas.setFillColor(WHITE)
        canvas.setFont(BOLD_FONT, 7.6)
        canvas.drawString(LEFT, PAGE_H - 8.3 * mm, "BACKBOND  |  DESIGN PARTNER SCANNER PILOT")
        canvas.setFillColor(CYAN)
        canvas.circle(PAGE_W - RIGHT - 2 * mm, PAGE_H - 6.6 * mm, 1.5 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(LINE)
    canvas.line(LEFT, 11.5 * mm, PAGE_W - RIGHT, 11.5 * mm)
    canvas.setFont(BODY_FONT, 6.8)
    canvas.setFillColor(MUTED)
    canvas.drawString(LEFT, 7.2 * mm, FOOTER)
    canvas.drawRightString(PAGE_W - RIGHT, 7.2 * mm, f"Page {doc.page}")
    canvas.restoreState()


def cover_background(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    canvas.setFillColor(BLUE)
    canvas.circle(PAGE_W + 10 * mm, PAGE_H - 15 * mm, 60 * mm, fill=1, stroke=0)
    canvas.setFillColor(CYAN)
    canvas.circle(PAGE_W - 16 * mm, PAGE_H - 2 * mm, 18 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#102A52"))
    canvas.circle(-5 * mm, 13 * mm, 55 * mm, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont(BOLD_FONT, 8)
    canvas.drawString(LEFT, PAGE_H - 18 * mm, "BACKBOND")
    canvas.setFillColor(colors.HexColor("#BFD1ED"))
    canvas.setFont(BODY_FONT, 6.8)
    canvas.drawString(LEFT, 7.2 * mm, FOOTER)
    canvas.drawRightString(PAGE_W - RIGHT, 7.2 * mm, "Page 1")
    canvas.restoreState()


def page_chrome(canvas, doc):
    if doc.page == 1:
        cover_background(canvas, doc)
    else:
        page_header_footer(canvas, doc)


class PartnerGuideDoc(BaseDocTemplate):
    def __init__(self, filename: str):
        super().__init__(
            filename, pagesize=A4, leftMargin=LEFT, rightMargin=RIGHT, topMargin=TOP, bottomMargin=BOTTOM,
            title="BackBond Design Partner Scanner Pilot - Quick Start v2", author="BackBond",
            subject="Authorized staging scan onboarding and operation",
        )
        body_frame = Frame(LEFT, BOTTOM + 3 * mm, CONTENT_W, PAGE_H - BOTTOM - TOP - 11 * mm, id="body")
        self.addPageTemplates(
            [PageTemplate(id="Guide", frames=[body_frame], onPage=page_chrome)]
        )


def build_story() -> list[Flowable]:
    story: list[Flowable] = []

    # ---- Cover (page 1) ----
    story.extend([
        Spacer(1, 20.9 * mm),
        p("DESIGN PARTNER QUICK START  ·  V2", "BBLabel"),
        p("BackBond AI Agent Scanner Pilot", "BBCoverTitle"),
        p("Connect your staging AI agent, run one authorized scan, and get your result — "
          "usually within a single approved window.", "BBCoverSub"),
        Spacer(1, 5.0 * mm),
    ])
    status = Table([
        [p('<font color="#24C8D8"><b>PRODUCTION STATUS</b></font>', "BBSmall")],
        [p('<font color="#FFFFFF"><b>Live and verified on sequence 20</b></font>', "BBH2")],
        [p('<font color="#DCE8FF">Scanner 0.5.0  |  Corpus 2026.6.24  |  HTTPS endpoint healthy  |  Auth enforcement active</font>', "BBBody")],
    ], colWidths=[CONTENT_W])
    status.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#102A52")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#355C90")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
    ]))
    story.append(status)
    story.extend([
        Spacer(1, 6.6 * mm),
        p('<font color="#FFFFFF"><b>This guide is for you if</b></font>', "BBH3"),
        p('<font color="#DCE8FF">You control a staging agent endpoint, you’ve approved the test in writing, '
          'and you want the fastest assisted path through the pilot.</font>', "BBBody"),
        Spacer(1, 2.8 * mm),
        p('<font color="#FFFFFF"><b>It is not for</b></font>', "BBH3"),
        p('<font color="#DCE8FF">Unapproved targets, denial-of-service testing, credential attacks, or production '
          'customer data. A scan result is never a claim of insurance, warranty, certification, or coverage.</font>', "BBBody"),
        Spacer(1, 11.0 * mm),
        p('<font color="#AFC2DF">Prepared 21 July 2026  |  Partner-facing operating guide</font>', "BBSmall"),
        PageBreak(),
    ])

    # ---- Page 2: 01 how it works + 02 what you send ----
    story.extend(section("01 / how the pilot works", "Six steps, one scan",
        "Even a friendly pilot starts with written authority and a safe staging target. After that, it moves "
        "fast. Blue steps are yours, green are ours, amber we do together:"))
    story.append(StepFlow([
        ("1 · You", "Approve the target and time window", "you"),
        ("2 · You", "Expose a staging endpoint", "you"),
        ("3 · You", "Test it with our checker", "you"),
        ("4 · Us", "Provision your connection pack", "us"),
        ("5 · Us", "Run and poll the scan", "us"),
        ("6 · Together", "Review the results", "together"),
    ], CONTENT_W))
    story.append(Spacer(1, 2.0 * mm))
    story.append(callout("The fastest safe path",
        "You send us your endpoint and its agent secret through the agreed secure channel. A BackBond operator "
        "provisions your tenant and runs the scan — you never handle BackBond production credentials. "
        "(Prefer to run it yourself? Ask about the self-run option.)", "green"))
    story.append(Spacer(1, 2.2 * mm))
    story.append(callout("Before anyone sends a single probe",
        "We need four things in writing: your permission, the exact staging hostname, the allowed time window, "
        "and a stop contact. This guide is an operational template, not legal advice — use counsel-approved "
        "terms for production systems or customer data.", "amber"))
    story.append(Spacer(1, 2.8 * mm))

    story.extend(section("02 / what you send us", "One short intake",
        "Send these six answers to your named BackBond contact. Ordinary email is fine for everything except the "
        "agent secret — that travels only through the secure channel we set up with you."))
    intake_rows = [
        ["Authority", "Your legal company name, confirmation you control the target, and written approval for BackBond to test it."],
        ["Target", "The exact final HTTPS URL. Staging only, no redirects, never a real-customer production endpoint."],
        ["Window", "Start and end time with timezone, plus any rate or maintenance constraints."],
        ["Stop contact", "A person we can reach by phone (or an agreed instant channel) for the whole window. They don’t need to sit watching logs — but they must be able to cut our access within minutes if asked."],
        ["Agent secret", "A dedicated bearer secret accepted only by the staging endpoint — sent through the agreed secure channel, never in this form."],
        ["Agent behavior", "Expected response shape, known tool access, and anything that must never be invoked."],
    ]
    story.append(data_table(["Item", "What to send"], intake_rows, [30 * mm, CONTENT_W - 30 * mm]))
    story.append(Spacer(1, 2.2 * mm))
    story.append(callout("Authorization text — copy, fill, approve",
        escape('"[Partner legal name] confirms that it controls [exact hostname] and authorizes BackBond to send '
               'benign application-layer prompts to that staging endpoint between [start] and [end, timezone]. The '
               'test excludes denial-of-service, credential attacks, lateral movement, persistence, and real '
               'customer data. Stop contact: [name, email, phone]."'), "blue"))
    story.append(Spacer(1, 2.2 * mm))
    story.append(callout("While approvals are in flight",
        "No need to wait for the green light: ask your BackBond contact for "
        '<font name="BBMono">conformance_check.py</font> (page 3) as soon as you submit the intake. It runs with '
        "just your endpoint URL and your own bearer secret — so you’re scan-ready the moment the window "
        "is approved.", "green"))
    story.append(PageBreak())

    # ---- Page 3: 03 endpoint ----
    story.extend(section("03 / your endpoint", "One prompt in, one JSON reply out",
        "The scanner sends a single authenticated POST and expects a single JSON response. Here’s the whole exchange:"))
    story.append(code_block(r"""
// What we send
POST https://agent.staging.example.com/scan
Authorization: Bearer <AGENT_BEARER_SECRET>
Content-Type: application/json
Accept-Encoding: identity

{"prompt":"Summarize the safe action you would take in this situation."}

// What your agent replies
HTTP/1.1 200 OK
Content-Type: application/json

{
  "response": "I would ask for confirmation before taking an irreversible action.",
  "actions": [
    { "tool_name": "request_confirmation",
      "action_class": "human_escalation",
      "arguments": {"reason": "irreversible action"} }
  ]
}
"""))
    story.append(Spacer(1, 2.2 * mm))
    story.append(p("Five rules your endpoint must follow", "BBH2"))
    for text in [
        "<b>Auth:</b> require the agreed bearer secret; a request without it must get 401 or 403.",
        "<b>Response:</b> return HTTP 200 with a JSON object that has a string field named "
        '<font name="BBMono">response</font>. That’s the only required field — see the note below on '
        '<font name="BBMono">actions</font>.',
        "<b>Network:</b> use the final public HTTPS hostname — no redirects, no private or metadata IPs.",
        "<b>Limits:</b> reply within 30 seconds, stay under 256 KiB, and don’t compress the response.",
        "<b>Safety:</b> staging agent only — synthetic data, reversible or mocked tools.",
    ]:
        story.append(bullet(text))
    story.append(Spacer(1, 2.0 * mm))
    story.append(callout("Is the actions array required?",
        'No — <font name="BBMono">response</font> is the only required field. '
        '<font name="BBMono">actions</font> is optional tool telemetry: include it if your agent can report the '
        "tools it invoked (it gives us direct evidence for the governance checks), and omit it — or send an "
        "empty array — when no tools ran. Dimensions we can’t observe show up as "
        '<font name="BBMono">uncovered_dimensions</font> in your result rather than hurting your score. Any other '
        "fields are ignored.", "blue"))
    story.append(Spacer(1, 2.2 * mm))
    story.append(p("Prove it’s ready in one command", "BBH2"))
    story.append(code_block(r"""
export AGENT_BEARER_SECRET="<load from your approved secret channel>"
python3 conformance_check.py --url https://agent.staging.example.com/scan

# reachable (authorized POST)     PASS
# valid JSON body                 PASS
# has string 'response' field     PASS
# response within 256 KiB         PASS
# rejects missing auth            PASS
# handles malformed body          PASS
# All required checks passed - endpoint is scan-ready.
""", "Six PASSes from conformance_check.py means you’re scan-ready"))
    story.append(PageBreak())

    # ---- Page 4: 04 connection pack + 05 running the scan ----
    story.extend(section("04 / what we send you", "The connection pack",
        "The pack pins down who’s who, what’s allowed, and exactly which scanner version you’re "
        "getting — so a re-run gives the same answer. Secret values always travel separately from this PDF."))
    pack_rows = [
        ["API base URL", link("https://scanner.backbond.ai", "https://scanner.backbond.ai") + " — HTTPS only."],
        ["Partner API key", "Tenant-scoped bearer key, delivered through the secure channel. Never in a ticket or chat."],
        ["Tenant ID", 'A unique ID such as <font name="BBMono">example-co</font> — used in both the header and the request body.'],
        ["Version pins", "Exact scanner version, corpus version, corpus snapshot hash, and seed to use verbatim."],
        ["Conformance checker", "The script on page 3, for validating your endpoint before the real scan."],
        ["Operating window", "Named BackBond operator, support route, credential expiry, rate limits — plus the request volume and typical duration your endpoint should expect."],
    ]
    story.append(data_table(["Item", "What it is"], pack_rows, [40 * mm, CONTENT_W - 40 * mm]))
    story.append(Spacer(1, 2.2 * mm))
    story.append(callout("Keep secrets out of documents",
        "This PDF contains no working credential — and neither should anything else you write down. Keep the "
        "BackBond API key and your endpoint secret in the approved secret channel only. Never paste either into "
        "email, chat, screenshots, result documents, or shell transcripts.", "amber"))
    story.append(Spacer(1, 2.8 * mm))

    story.extend(section("05 / running the scan", "Submit, then poll",
        "On the assisted path, this part is us — a BackBond operator runs these from a secured shell. You’d "
        "only run them yourself on the self-run path. Either way: replace every angle-bracket placeholder with the "
        "exact value from the connection pack."))
    story.append(code_block(r"""
# 1. Submit (both secrets already loaded in the shell environment)
curl -sS -X POST "https://scanner.backbond.ai/scanner/v1/scans" \
  -H "Authorization: Bearer $BACKBOND_API_KEY" \
  -H "X-Tenant-Id: example-co" \
  -H "Content-Type: application/json" \
  --data-binary @- <<JSON
{
  "tenant_id": "example-co",
  "agent_endpoint": { "host": "agent.staging.example.com", "transport": "rest" },
  "credential_ref": "example-co-staging-001",
  "credential_secret": "$AGENT_BEARER_SECRET",
  "corpus_snapshot_hash": "<FROM_CONNECTION_PACK>",
  "scanner_version": "0.5.0", "corpus_version": "2026.6.24", "seed": 42,
  "scan_mode": "live_probe", "k_replicates": 5
}
JSON
# -> HTTP/1.1 202 Accepted {"scan_id":"scn_example_01","status":"accepted"}

# 2. Poll every 30-60 seconds until status is "complete" or the window ends.
#    Don't poll tighter than that - each tenant is rate-limited (HTTP 429 if you loop too fast).
curl -sS "https://scanner.backbond.ai/scan/scn_example_01?audience=customer" \
  -H "Authorization: Bearer $BACKBOND_API_KEY" -H "X-Tenant-Id: example-co"
"""))
    story.append(Spacer(1, 2.2 * mm))
    story.append(callout("If a scan fails",
        "Stop. Keep the scan ID and the non-secret error code. Don’t rotate credentials, change the endpoint, "
        "or retry repeatedly without first coordinating with the stop contact.", "amber"))
    story.append(PageBreak())

    # ---- Page 5: 06 reading result + 07 troubleshooting ----
    story.extend(section("06 / reading your result", "What comes back",
        "A sample result (illustrative only — not a real partner score):"))
    story.append(callout("Assessment boundary",
        "This is an <b>AIUC-1-mapped technical assessment</b>, not an AIUC certification or compliance decision. "
        "Any measurements outside that mapping are separately labeled <b>BackBond Extensions</b>.", "blue"))
    story.append(Spacer(1, 2.0 * mm))
    story.append(code_block(r"""
{
  "status": "complete",
  "are_total": 76,
  "subscores": { "GOVERNANCE": 18.5, "ROBUSTNESS": 20.1, "BIAS_FAIRNESS": 17.8, "OPS": 20.0 },
  "band": "B",
  "confidence": 0.84,
  "evidence_refs": [ {"dimension": "ROBUSTNESS", "verdict": "measured", "artifact_ref": "..."} ],
  "uncovered_dimensions": [
    {"dim_id": "P4.3_KILL_SWITCH", "reason": "attestation evidence unavailable"}
  ],
  "scanner_version": "0.5.0", "corpus_version": "2026.6.24", "seed": 42,
  "run_timestamp": "2026-07-23T10:18:00Z"
}
"""))
    story.append(Spacer(1, 2.0 * mm))
    for text in [
        "<b>are_total</b> — overall measured exposure, 0 to 100.",
        "<b>subscores</b> — four component scores; the lowest ones point to your best remediation opportunities.",
        "<b>band</b> — a summary label for this scan only. It is not an insurance, warranty, or certification decision.",
        "<b>confidence</b> — how complete and consistent the evidence was for this run.",
        "<b>evidence_refs</b> — links every scored finding to a retained evidence record.",
        "<b>uncovered_dimensions</b> — areas we couldn’t measure. Treat these as unknown, not as fine.",
        "<b>version pins</b> — scanner version, corpus version, seed, and timestamp identify the exact run.",
    ]:
        story.append(bullet(text))
    story.append(Spacer(1, 2.0 * mm))
    # [Improvement 1 from the 2026-07-21 design-partner test]
    story.append(callout("Telling a real score from this sample",
        "A real result always carries its own <font name=\"BBMono\">scanner_version</font>, "
        "<font name=\"BBMono\">corpus_version</font>, <font name=\"BBMono\">seed</font>, "
        "<font name=\"BBMono\">run_timestamp</font>, and <font name=\"BBMono\">evidence_refs</font> that resolve to "
        "retained records. If a result is missing those pins or arrives with no evidence_refs, treat it as a sample "
        "or placeholder — not a measurement — and ask your BackBond operator to confirm the run.", "blue"))
    story.append(Spacer(1, 2.2 * mm))
    story.append(p("What happens next", "BBH2"))
    story.append(p("We walk through the result with you — never with raw secrets or private prompts in shared "
                   "documents — and help you split the quick fixes from the deeper control gaps. You set the "
                   "priorities. If the target, secret, deployment, or scope changes, a re-scan needs a newly "
                   "approved window."))
    story.append(PageBreak())

    story.extend(section("07 / if something goes wrong", "Common failures, safe responses",
        "Most first-run problems are contract or credential mismatches, not scanner defects."))
    error_rows = [
        ["401 bearer_required", "BackBond API key missing on submit or poll.", "Load the connection-pack key and use the Bearer prefix."],
        ["401 authorization_invalid", "API key wrong, expired, or out of scope.", "Stop and ask BackBond to verify the key. Don’t paste it into chat."],
        ["409 tenant mismatch", "Header tenant differs from the request body.", "Use the exact same tenant ID in both places."],
        ["422 validation error", "A required field is missing or malformed.", "Compare the request with the connection pack; check the named field."],
        ["429 rate_limited", "Submitting or polling faster than your tenant’s limit.", "Wait the Retry-After seconds, then resume a 30-60 s cadence."],
        ["502 endpoint HTTP error", "Your endpoint returned an error.", "Check endpoint logs, auth, JSON shape, and the agent’s mode."],
        ["504 endpoint unreachable", "TLS, DNS, network, or 30-second timeout.", "Re-run conformance; check allowlists, final URL, load, certificate."],
        # [Improvement 2 from the 2026-07-21 design-partner test]
        ["No HTTP response / connection reset", "Your endpoint crashed or dropped the connection before replying (the conformance checker shows this as no HTTP status).", "Check endpoint logs for an unhandled error — often a malformed request body — confirm it stays up under a single request, then re-run conformance."],
    ]
    story.append(data_table(["You see", "It usually means", "Do this"], error_rows, [42 * mm, 52 * mm, CONTENT_W - 94 * mm]))
    story.append(PageBreak())

    # ---- Page 6: stop conditions + 08 final checklist ----
    story.append(p("Stop the scan immediately when", "BBH2"))
    for text in [
        "You ask us to stop, or the approved window closes.",
        "The endpoint starts serving real customer data or irreversible production tools.",
        "Unexpected traffic, rate impact, authentication failures, or a scope mismatch appears.",
        "Anyone is unsure whether the exact host is authorized.",
    ]:
        story.append(bullet(text))
    story.append(Spacer(1, 2.0 * mm))
    story.append(callout("Never troubleshoot by widening scope",
        "Don’t disable authentication, follow redirects, add private IPs, relax the response cap, or share "
        "secrets in ordinary support channels. Coordinate with the named partner and BackBond operators instead.", "amber"))
    story.append(Spacer(1, 2.8 * mm))

    story.extend(section("08 / final checklist", "Ready when both sides can tick every box"))
    story.append(p("Everything in this guide has been checked against the live service (sequence 20 acceptance: "
                   "scanner root, SLA endpoint, submit and poll auth, endpoint contract — all PASS). So our "
                   "side is proven. The one piece only you can prove is your own endpoint’s conformance check.", "BBSmall"))
    story.append(Spacer(1, 2.0 * mm))
    story.append(data_table(["You confirm", "BackBond confirms"], [
        ["Written authority, exact staging hostname, window, stop contact.", "Tenant, API key, version pins, corpus hash, seed, named operator."],
        ["HTTPS endpoint, bearer auth, 200 JSON response, no redirects.", "Conformance PASS and scanner health PASS."],
        ["Synthetic data, reversible tools, no live-customer side effects.", "Accepted scan ID, customer-view poll, complete result envelope."],
        ["Result review and any approved follow-up window.", "Evidence pins, debrief, remediation notes, re-scan decision."],
    ], [CONTENT_W / 2, CONTENT_W / 2]))
    story.append(Spacer(1, 2.8 * mm))
    story.append(p("Links and references", "BBH2"))
    for label, url in [
        ("BackBond scanner", "https://scanner.backbond.ai/"),
        ("BackBond scanner SLA endpoint", "https://scanner.backbond.ai/scanner/v1/sla"),
        ("NIST SP 800-115: Technical Guide to Information Security Testing and Assessment", "https://csrc.nist.gov/pubs/sp/800/115/final"),
        ("NIST glossary: Rules of Engagement", "https://csrc.nist.gov/glossary/term/rules_of_engagement"),
        ("US Department of Justice CFAA charging policy", "https://www.justice.gov/archives/opa/pr/department-justice-announces-new-policy-charging-cases-under-computer-fraud-and-abuse-act"),
    ]:
        story.append(bullet(link(label, url)))
    story.append(Spacer(1, 2.0 * mm))
    story.append(p("Questions? Use your named BackBond operator and the agreed secure channel. Never send tokens or "
                   "secrets in ordinary email, chat, screenshots — or this PDF.", "BBSmall"))

    return story


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = PartnerGuideDoc(str(OUT_FILE))
    doc.build(build_story())
    METADATA_FILE.write_text(json.dumps({
        "artifact_id": "BEAAA-18123-design-partner-quick-start-v2",
        "persona_audience": "external",
        "scanner_version": "0.5.0",
        "corpus_version": "2026.6.24",
        "probe-set-version": "unchanged",
        "rubric_point_id": "N/A",
        "contract_version": "are-scanner-api-v1@v1.0",
        "brand_backbone_version": BRAND_VERSION,
        "brand_backbone_sha256": BRAND_SHA256,
        "brand_backbone_verified": True,
    }, indent=2, sort_keys=True) + "\n")
    print(OUT_FILE)


if __name__ == "__main__":
    main()

"""Address -> report card web page.   python -m civic.cli serve [--host 0.0.0.0] [--port 8080]

The form POSTs (so the address never lands in a URL, browser history or the access log).
The address goes only to the Census geocoder, same as `civic.cli lookup`; nothing is stored.
The HTML lives in this file because .gitignore excludes *.html.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from flask import Flask, render_template_string, request

from . import lookup as lk
from .util import STATE_FIPS

log = logging.getLogger(__name__)
app = Flask(__name__)

SEATS = [  # display order
    (("federal", "upper"), "US Senate"),
    (("federal", "lower"), "US House"),
    (("state", "upper"), "State Senate"),
    (("state", "lower"), "State House"),
]
SEAT_LABEL = dict(SEATS)
STATES = sorted(STATE_FIPS)


@app.template_filter("money")
def _money(v):
    if v is None:
        return "—"
    v = float(v)
    if abs(v) >= 1e6:
        return f"${v / 1e6:,.1f}M"
    if abs(v) >= 1e3:
        return f"${v / 1e3:,.0f}K"
    return f"${v:,.0f}"


@app.template_filter("num")
def _num(v):
    return "—" if v is None else f"{int(v):,}"


@app.template_filter("pct")
def _pct(v):
    return "—" if v is None else f"{float(v):.1f}%"


def _ideology(dim1) -> dict | None:
    """DW-NOMINATE dim1 (-1 liberal .. +1 conservative) -> marker position and label."""
    if dim1 is None:
        return None
    x = float(dim1) if not isinstance(dim1, float) else dim1
    x = max(-1.0, min(1.0, x))
    word = ("very liberal" if x <= -0.5 else "liberal" if x <= -0.2 else "moderate" if x < 0.2
            else "conservative" if x < 0.5 else "very conservative")
    return {"left": round((x + 1) * 50, 1), "value": f"{x:+.3f}", "word": word}


def _group(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    out = []
    for key, label in SEATS:
        members = [dict(r, ideo=_ideology(r.get("nominate_dim1")))
                   for r in rows if (r["level"], r["chamber"]) == key]
        if members:
            out.append((label, members))
    return out


@app.route("/", methods=["GET", "POST"])
def index():
    form = {k: (request.form.get(k) or "").strip() for k in ("street", "city", "state", "zip")}
    ctx = {"form": form, "states": STATES, "groups": None, "error": None,
           "matched": None, "districts": []}
    if request.method == "POST":
        if not form["street"] or not form["state"] or not (form["city"] or form["zip"]):
            ctx["error"] = "Enter a street address, state, and a city or ZIP code."
        else:
            one_line = ", ".join(x for x in (form["street"], form["city"],
                                             f"{form['state']} {form['zip']}".strip()) if x)
            try:
                m = lk.geocode_match(one_line)
            except Exception:
                log.exception("geocoder request failed")   # deliberately no address in the log
                m = None
                ctx["error"] = "The Census geocoder didn't respond. Try again in a minute."
            if m and not ctx["error"]:
                lon, lat, ctx["matched"] = m
                try:
                    ctx["districts"] = sorted(
                        lk.districts(lon, lat),
                        key=lambda d: [k for k, _ in SEATS].index((d["level"], d["chamber"])))
                    ctx["groups"] = _group(lk.officials(lon, lat))
                except Exception:
                    log.exception("database lookup failed")
                    ctx["error"] = "Couldn't reach the civic database."
            elif not ctx["error"]:
                ctx["error"] = ("The Census geocoder couldn't match that address. "
                                "Check the street number and spelling.")
    return render_template_string(PAGE, seat_label=SEAT_LABEL, **ctx)


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Civic Report Card</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--muted:#6b6b70;--line:#e3e1dc;--accent:#2f5d8a;
 --dem:#2f62b5;--rep:#c0392b;--ind:#7a5aa6;--bad:#b3261e;--good:#2e7d32;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1e1e21;--ink:#ececee;--muted:#9a9aa1;
 --line:#2f2f34;--accent:#7fb0e0;--dem:#6f9ce6;--rep:#e57368;--ind:#b195d6;--bad:#ef8a80;--good:#7cc47f;--chip:#26292e}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1000px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:1.6rem;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 22px}
form{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;
 display:grid;grid-template-columns:2fr 1.2fr .8fr .8fr auto;gap:12px;align-items:end}
label{display:block;font-size:.8rem;color:var(--muted);margin-bottom:4px}
input,select{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:8px;
 background:var(--bg);color:var(--ink);font:inherit}
button{padding:10px 18px;border:0;border-radius:8px;background:var(--accent);color:#fff;font:inherit;
 font-weight:600;cursor:pointer}
@media (prefers-color-scheme:dark){button{color:#111}}
@media (max-width:760px){form{grid-template-columns:1fr 1fr}.f-street{grid-column:1/-1}button{grid-column:1/-1}}
.err{margin:16px 0;padding:12px 14px;border-radius:8px;border:1px solid var(--bad);color:var(--bad)}
.matched{margin:22px 0 8px;font-weight:600}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}
.chip{background:var(--chip);border-radius:999px;padding:4px 12px;font-size:.85rem}
h2{font-size:1.05rem;margin:28px 0 10px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.head{display:flex;gap:12px;align-items:center;margin-bottom:12px}
.photo{width:56px;height:56px;border-radius:50%;object-fit:cover;background:var(--chip);flex:none}
.name{font-weight:650;font-size:1.05rem}
.name a{color:inherit;text-decoration:none}.name a:hover{text-decoration:underline}
.meta{color:var(--muted);font-size:.85rem}
.party{display:inline-block;font-size:.72rem;font-weight:700;padding:1px 7px;border-radius:4px;color:#fff;margin-right:6px}
.p-D{background:var(--dem)}.p-R{background:var(--rep)}.p-other{background:var(--ind)}
dl{display:grid;grid-template-columns:1fr 1fr;gap:10px 14px;margin:0}
dt{font-size:.75rem;color:var(--muted)}dd{margin:0;font-weight:600;font-variant-numeric:tabular-nums}
dd small{font-weight:400;color:var(--muted)}
.sec{grid-column:1/-1;border-top:1px solid var(--line);padding-top:8px;font-size:.72rem;
 text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.ideo{grid-column:1/-1}
.bar{position:relative;height:8px;border-radius:4px;margin:6px 0 2px;
 background:linear-gradient(90deg,var(--dem),var(--chip) 50%,var(--rep))}
.mark{position:absolute;top:-4px;width:4px;height:16px;border-radius:2px;background:var(--ink);transform:translateX(-50%)}
.ends{display:flex;justify-content:space-between;font-size:.7rem;color:var(--muted)}
.none{color:var(--muted);font-size:.9rem}
footer{margin-top:40px;color:var(--muted);font-size:.8rem}
</style></head><body><main>
<h1>Civic Report Card</h1>
<p class="sub">Find who represents an address in Congress and the state legislature, and how they're doing.</p>

<form method="post" autocomplete="on">
 <div class="f-street"><label for="street">Street address</label>
  <input id="street" name="street" value="{{ form.street }}" placeholder="123 Main St" autocomplete="address-line1" required></div>
 <div><label for="city">City</label>
  <input id="city" name="city" value="{{ form.city }}" autocomplete="address-level2"></div>
 <div><label for="state">State</label>
  <select id="state" name="state" autocomplete="address-level1" required>
   <option value=""></option>
   {% for s in states %}<option {% if s == form.state %}selected{% endif %}>{{ s }}</option>{% endfor %}
  </select></div>
 <div><label for="zip">ZIP</label>
  <input id="zip" name="zip" value="{{ form.zip }}" inputmode="numeric" pattern="\d{5}(-\d{4})?" autocomplete="postal-code"></div>
 <button type="submit">Look up</button>
</form>

{% if error %}<div class="err">{{ error }}</div>{% endif %}

{% if matched %}
 <div class="matched">{{ matched }}</div>
 {% if districts %}<div class="chips">
  {% for d in districts %}<span class="chip">{{ seat_label[(d.level, d.chamber)] }}: {{ d.name or d.district }}</span>{% endfor %}
 </div>{% endif %}

 {% if not groups %}
  <p class="none">No officials found. Are boundaries and rosters loaded for this state?</p>
 {% endif %}

 {% for label, members in groups or [] %}
  <h2>{{ label }}</h2>
  <div class="grid">
  {% for r in members %}
   <div class="card">
    <div class="head">
     {% if r.photo_url %}<img class="photo" src="{{ r.photo_url }}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
     {% else %}<div class="photo"></div>{% endif %}
     <div>
      <div class="name">{% if r.url %}<a href="{{ r.url }}" target="_blank" rel="noopener">{{ r.full_name }}</a>{% else %}{{ r.full_name }}{% endif %}</div>
      <div class="meta"><span class="party {{ 'p-' ~ r.party_code if r.party_code in ('D','R') else 'p-other' }}">{{ r.party_code or '?' }}</span>
       {{ r.party or '' }} · {{ r.state }}{% if r.district %}-{{ r.district }}{% endif %}</div>
     </div>
    </div>
    <dl>
     <div class="sec">Floor votes</div>
     {% if r.votes_eligible %}
     <div><dt>Missed</dt><dd>{{ r.missed_pct|pct }}</dd></div>
     <div><dt>Votes missed / eligible</dt><dd>{{ r.votes_missed|num }} <small>of {{ r.votes_eligible|num }}</small></dd></div>
     {% else %}<div class="none" style="grid-column:1/-1">No roll-call data loaded yet.</div>{% endif %}

     <div class="sec">Legislation</div>
     <div><dt>Sponsored</dt><dd>{{ r.bills_sponsored|num }}</dd></div>
     <div><dt>Enacted</dt><dd>{{ r.bills_enacted|num }}</dd></div>
     <div><dt>Cosponsored</dt><dd>{{ r.bills_cosponsored|num }}</dd></div>
     <div><dt>Cross-party cosponsorships</dt><dd>{{ r.cross_party_pct|pct }}
       {% if r.cosponsorships_scored %}<small>of {{ r.cosponsorships_scored|num }}</small>{% endif %}</dd></div>

     {% if r.receipts is not none %}
     <div class="sec">Campaign finance{% if r.finance_cycle %} · {{ r.finance_cycle }} cycle{% endif %}</div>
     <div><dt>Raised</dt><dd>{{ r.receipts|money }}</dd></div>
     <div><dt>Cash on hand</dt><dd>{{ r.cash_on_hand|money }}</dd></div>
     <div><dt>Individuals</dt><dd>{{ r.individual_contributions|money }}</dd></div>
     <div><dt>PACs</dt><dd>{{ r.pac_contributions|money }}</dd></div>
     {% endif %}

     {% if r.ideo %}
     <div class="sec">Ideology · DW-NOMINATE{% if r.ideology_congress %} · {{ r.ideology_congress }}th Congress{% endif %}</div>
     <div class="ideo">
      <dd>{{ r.ideo.value }} <small>{{ r.ideo.word }}</small></dd>
      <div class="bar"><div class="mark" style="left:{{ r.ideo.left }}%"></div></div>
      <div class="ends"><span>liberal −1</span><span>+1 conservative</span></div>
     </div>
     {% endif %}
    </dl>
   </div>
  {% endfor %}
  </div>
 {% endfor %}
{% endif %}

<footer>Addresses are sent only to the US Census geocoder and are not logged or stored.
Data: congress-legislators, Open States, Census TIGER, House/Senate clerks, Congress.gov, OpenFEC, Voteview.</footer>
</main></body></html>
"""

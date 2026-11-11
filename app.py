"""Review app: see each proposed CRM change with evidence, approve or reject.

    python app.py            # http://127.0.0.1:5050

Approving a proposal writes it to the CRM immediately through the API. Nothing is
written without a click.
"""
from __future__ import annotations

import json
import subprocess
import sys

from flask import Flask, abort, flash, redirect, render_template, request, url_for

from bellhaven_sync.apply import apply_proposal
from bellhaven_sync.config import CRM_BASE_URL, CRM_TOKEN
from bellhaven_sync.crm import CRM
from bellhaven_sync.store import Store

app = Flask(__name__)
app.secret_key = "local-review-app"

KIND_LABELS = {
    "create": "New account",
    "update": "Field fix",
    "reparent": "Re-parent",
    "chow": "Change of ownership (SOP)",
    "duplicate": "Duplicate",
    "divest_review": "Not on website",
    "divest_chow": "Sold (CHOW link)",
    "divest_duplicate": "Sold (retire copy)",
    "parent_note": "Operator absorbed",
}
KIND_ORDER = ["chow", "reparent", "update", "create", "duplicate", "divest_chow", "divest_duplicate", "divest_review", "parent_note"]


def store() -> Store:
    return Store()


@app.context_processor
def inject():
    return {"KIND_LABELS": KIND_LABELS, "counts": store().counts(),
            "crm_browser": f"{CRM_BASE_URL}/crm/{CRM_TOKEN}/accounts", "reviewer": request.cookies.get("reviewer", "")}


@app.template_filter("pretty")
def pretty(v):
    return json.dumps(v, indent=2)


@app.route("/")
def queue():
    status = request.args.get("status", "pending")
    kind = request.args.get("kind", "")
    rows = store().list_proposals(None if status == "all" else status)
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    rows.sort(key=lambda r: (KIND_ORDER.index(r["kind"]) if r["kind"] in KIND_ORDER else 99,
                             {"high": 0, "medium": 1, "low": 2}[r["confidence"]], r["title"]))
    kinds = sorted({r["kind"] for r in store().list_proposals(None)}, key=lambda k: KIND_ORDER.index(k) if k in KIND_ORDER else 99)
    return render_template("queue.html", rows=rows, status=status, kind=kind, kinds=kinds)


@app.route("/proposal/<int:pid>")
def proposal(pid):
    p = store().get_proposal(pid)
    if not p:
        abort(404)
    return render_template("proposal.html", p=p)


def _who() -> str:
    return request.form.get("reviewer") or request.cookies.get("reviewer") or "reviewer"


@app.post("/proposal/<int:pid>/approve")
def approve(pid):
    s = store()
    p = s.get_proposal(pid)
    if not p or p["status"] not in ("pending", "withdrawn"):
        abort(400)
    s.decide(pid, "approved", _who(), request.form.get("note", ""))
    result = apply_proposal(s, CRM(), pid)
    flash(("Approved and written to CRM: " if result["ok"] else "Approved but CRM write FAILED: ") + p["title"],
          "ok" if result["ok"] else "err")
    resp = redirect(request.form.get("next") or url_for("proposal", pid=pid))
    resp.set_cookie("reviewer", _who())
    return resp


@app.post("/proposal/<int:pid>/reject")
def reject(pid):
    s = store()
    p = s.get_proposal(pid)
    if not p or p["status"] not in ("pending", "withdrawn"):
        abort(400)
    s.decide(pid, "rejected", _who(), request.form.get("note", ""))
    flash("Rejected: " + p["title"], "ok")
    resp = redirect(request.form.get("next") or url_for("queue"))
    resp.set_cookie("reviewer", _who())
    return resp


@app.post("/proposal/<int:pid>/reopen")
def reopen(pid):
    s = store()
    p = s.get_proposal(pid)
    if not p or p["status"] not in ("rejected", "failed"):
        abort(400)
    s.reopen(pid, _who())
    flash("Re-opened: " + p["title"], "ok")
    return redirect(url_for("proposal", pid=pid))


@app.post("/bulk")
def bulk():
    s = store()
    ids = [int(x) for x in request.form.getlist("ids")]
    action = request.form.get("action")
    n = 0
    for pid in ids:
        p = s.get_proposal(pid)
        if not p or p["status"] not in ("pending", "withdrawn"):
            continue
        if action == "approve":
            s.decide(pid, "approved", _who(), "bulk")
            apply_proposal(s, CRM(), pid)
        elif action == "reject":
            s.decide(pid, "rejected", _who(), "bulk")
        n += 1
    flash(f"{action}d {n} proposal(s)", "ok")
    return redirect(url_for("queue"))


@app.route("/matches")
def matches():
    return render_template("matches.html", rows=store().matches())


@app.route("/runs")
def runs():
    s = store()
    return render_template("runs.html", runs=s.runs(), audit=s.audit_log())


@app.post("/run")
def run_now():
    out = subprocess.run([sys.executable, "-m", "bellhaven_sync.pipeline"], capture_output=True, text=True)
    flash(("Pipeline run finished. " if out.returncode == 0 else "Pipeline FAILED. ") + (out.stdout + out.stderr)[-600:],
          "ok" if out.returncode == 0 else "err")
    return redirect(url_for("queue"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)

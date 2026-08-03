"""Human-facing read-only views. No write path exists here by design."""
import datetime
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import db

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


def ago(ts: float) -> str:
    d = datetime.datetime.now().timestamp() - ts
    for unit, s in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= s:
            return f"{int(d // s)}{unit} ago"
    return "just now"


templates.env.filters["ago"] = ago


def _render(request, name, **ctx):
    with db.conn() as c:
        channels = [dict(r) for r in c.execute(
            "SELECT name FROM channels ORDER BY name").fetchall()]
    return templates.TemplateResponse(request, name, {"channels_nav": channels, **ctx})


@router.get("/", response_class=HTMLResponse)
def home(request: Request, sort: str = "hot"):
    order = "p.score DESC, p.created_at DESC" if sort == "hot" else "p.created_at DESC"
    with db.conn() as c:
        posts = c.execute(f"""
            SELECT p.id, p.title, p.body, p.score, p.created_at, a.name AS author,
                   a.model, ch.name AS channel,
                   (SELECT COUNT(*) FROM comments cm WHERE cm.post_id=p.id) AS n_comments
            FROM posts p JOIN agents a ON a.id=p.agent_id JOIN channels ch ON ch.id=p.channel_id
            ORDER BY {order} LIMIT 50""").fetchall()
    return _render(request, "home.html", posts=[dict(r) for r in posts], sort=sort)


@router.get("/c/{channel}", response_class=HTMLResponse)
def channel_view(request: Request, channel: str):
    with db.conn() as c:
        ch = c.execute("SELECT * FROM channels WHERE name = ?", (channel,)).fetchone()
        if not ch:
            raise HTTPException(404)
        posts = c.execute("""
            SELECT p.id, p.title, p.body, p.score, p.created_at, a.name AS author, a.model,
                   (SELECT COUNT(*) FROM comments cm WHERE cm.post_id=p.id) AS n_comments
            FROM posts p JOIN agents a ON a.id=p.agent_id
            WHERE p.channel_id=? ORDER BY p.created_at DESC LIMIT 100""", (ch["id"],)).fetchall()
    return _render(request, "channel.html", channel=dict(ch), posts=[dict(r) for r in posts])


@router.get("/p/{post_id}", response_class=HTMLResponse)
def post_view(request: Request, post_id: int):
    with db.conn() as c:
        p = c.execute("""
            SELECT p.*, a.name AS author, a.model, ch.name AS channel
            FROM posts p JOIN agents a ON a.id=p.agent_id JOIN channels ch ON ch.id=p.channel_id
            WHERE p.id=?""", (post_id,)).fetchone()
        if not p:
            raise HTTPException(404)
        rows = c.execute("""
            SELECT cm.id, cm.parent_id, cm.body, cm.score, cm.created_at,
                   a.name AS author, a.model
            FROM comments cm JOIN agents a ON a.id=cm.agent_id
            WHERE cm.post_id=? ORDER BY cm.created_at""", (post_id,)).fetchall()
    # build a nested tree
    nodes = {r["id"]: {**dict(r), "children": []} for r in rows}
    tree = []
    for n in nodes.values():
        if n["parent_id"] and n["parent_id"] in nodes:
            nodes[n["parent_id"]]["children"].append(n)
        else:
            tree.append(n)
    return _render(request, "post.html", post=dict(p), comments=tree)


@router.get("/missions", response_class=HTMLResponse)
def missions_view(request: Request, status: str = "open", kind: str = ""):
    q = "SELECT m.*, a.name AS author FROM missions m JOIN agents a ON a.id=m.created_by WHERE 1=1"
    args = []
    if status in ("open", "complete"):
        q += " AND m.status=?"; args.append(status)
    if kind in ("goal", "blocker"):
        q += " AND m.kind=?"; args.append(kind)
    q += " ORDER BY m.created_at DESC LIMIT 100"
    with db.conn() as c:
        rows = c.execute(q, args).fetchall()
        missions = []
        for r in rows:
            d = dict(r)
            d["n_contrib"] = c.execute(
                "SELECT COUNT(*) FROM contributions WHERE mission_id=? AND status='published'",
                (r["id"],)).fetchone()[0]
            missions.append(d)
    return _render(request, "missions.html", missions=missions, status=status, kind=kind)


@router.get("/m/{mission_id}", response_class=HTMLResponse)
def mission_view(request: Request, mission_id: int):
    with db.conn() as c:
        m = c.execute("""SELECT m.*, a.name AS author, sa.name AS synth_author
                         FROM missions m JOIN agents a ON a.id=m.created_by
                         LEFT JOIN agents sa ON sa.id=m.synthesized_by
                         WHERE m.id=?""", (mission_id,)).fetchone()
        if not m:
            raise HTTPException(404)
        rows = c.execute("""
            SELECT ct.id, ct.parent_id, ct.kind, ct.body, ct.score, ct.created_at,
                   a.name AS author, a.model
            FROM contributions ct JOIN agents a ON a.id=ct.agent_id
            WHERE ct.mission_id=? AND ct.status='published'
            ORDER BY ct.created_at""", (mission_id,)).fetchall()
    nodes = {r["id"]: {**dict(r), "children": []} for r in rows}
    tree = []
    for n in nodes.values():
        if n["parent_id"] and n["parent_id"] in nodes:
            nodes[n["parent_id"]]["children"].append(n)
        else:
            tree.append(n)
    return _render(request, "mission.html", mission=dict(m), contributions=tree,
                   n_contrib=len(rows))


@router.get("/agents", response_class=HTMLResponse)
def agents_view(request: Request):
    with db.conn() as c:
        rows = c.execute("""
            SELECT a.name, a.model, a.operator, a.purpose, a.karma, a.created_at,
                   (SELECT COUNT(*) FROM posts p WHERE p.agent_id=a.id) AS posts,
                   (SELECT COUNT(*) FROM comments cm WHERE cm.agent_id=a.id) AS comments
            FROM agents a ORDER BY a.karma DESC""").fetchall()
    return _render(request, "agents.html", agents=[dict(r) for r in rows])


@router.get("/observatory", response_class=HTMLResponse)
def observatory(request: Request, since: float = 0, until: float = 0, type: str = ""):
    q, args = "SELECT * FROM events WHERE 1=1", []
    if since:
        q += " AND ts >= ?"; args.append(since)
    if until:
        q += " AND ts <= ?"; args.append(until)
    if type:
        q += " AND type = ?"; args.append(type)
    q += " ORDER BY ts DESC LIMIT 200"
    with db.conn() as c:
        rows = c.execute(q, args).fetchall()
        types = [r[0] for r in c.execute("SELECT DISTINCT type FROM events ORDER BY type")]
        counts = dict(c.execute(
            "SELECT type, COUNT(*) FROM events GROUP BY type").fetchall())
    events = []
    for r in rows:
        e = dict(r)
        e["payload"] = json.loads(e["payload"])
        events.append(e)
    return _render(request, "observatory.html", events=events, types=types,
                   counts=counts, f_since=since, f_until=until, f_type=type)

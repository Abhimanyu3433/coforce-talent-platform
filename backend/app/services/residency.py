"""Residency engine.

Residency = continuous time on the same project / account. Consecutive stints separated by
<= RESIDENCY_GAP_DAYS are merged ("gaps and islands"). Results are materialised into the
`residency_snapshot` table (one row per employee) so dashboards and search stay fast.
"""
from __future__ import annotations

import pandas as pd

from .. import config
from ..db import one, rows, scalar
from .errors import NotFound

BANDS = [(12, "<12m"), (18, "12-18m"), (24, "18-24m"), (36, "24-36m"), (10 ** 6, "36m+")]
BAND_ORDER = ["<12m", "12-18m", "18-24m", "24-36m", "36m+", "On bench", "N/A"]
AGING_BUCKETS = ["0-15d", "16-30d", "31-60d", "61-90d", "90d+"]
MONTH = 30.44


def islands(al: pd.DataFrame, key: str | None, gap_days: int) -> pd.DataFrame:
    """Merge stints per (emp_id, key) whose gap <= gap_days. Input needs emp_id, start, end (+ key)."""
    a = al.copy()
    if key is None:
        key = "_all"
        a[key] = 1
    a = a.sort_values(["emp_id", key, "start"])
    g = a.groupby(["emp_id", key], sort=False)
    a["run_end"] = g["end"].cummax()
    prev_end = a.groupby(["emp_id", key], sort=False)["run_end"].shift()
    a["new"] = prev_end.isna() | ((a["start"] - prev_end).dt.days > gap_days)
    a["isl"] = a.groupby(["emp_id", key], sort=False)["new"].cumsum()
    out = (a.groupby(["emp_id", key, "isl"], sort=False)
             .agg(start=("start", "min"), end=("end", "max"), stints=("start", "size")).reset_index())
    out["months"] = (out["end"] - out["start"]).dt.days / MONTH
    return out


def band_for(status: str, function: str, months) -> str:
    if function != "Delivery":
        return "N/A"
    if status in ("bench", "training"):
        return "On bench"
    if months is None or pd.isna(months):
        return "N/A"
    for cut, label in BANDS:
        if months < cut:
            return label
    return "36m+"


def compute_residency(conn, as_of=None) -> pd.DataFrame:
    today = pd.Timestamp(as_of or config.as_of())
    gap = config.RESIDENCY_GAP_DAYS
    emp = pd.read_sql("SELECT emp_id, function, status, date_of_joining, bench_since FROM employees", conn)
    al = pd.read_sql("""SELECT a.emp_id, a.project_id, a.account_id, a.start_date, a.end_date,
                               ac.rotation_threshold_months AS thr, ac.cool_off_months AS cool
                        FROM allocations a JOIN accounts ac USING(account_id)""", conn)
    al["start"] = pd.to_datetime(al["start_date"])
    al["raw_end"] = pd.to_datetime(al["end_date"])
    al = al[al["start"] <= today].copy()
    al["end"] = al["raw_end"].clip(upper=today)

    # current allocation = latest-starting stint that spans today
    cur = (al[al["raw_end"] > today].sort_values("start").groupby("emp_id").tail(1)
             [["emp_id", "project_id", "account_id", "raw_end", "thr"]]
             .rename(columns={"project_id": "current_project_id", "account_id": "current_account_id",
                              "raw_end": "planned_rolloff", "thr": "rotation_threshold_months"}))

    pi = islands(al, "project_id", gap)
    ai = islands(al, "account_id", gap)
    cur_p = pi[pi["end"] >= today][["emp_id", "project_id", "start", "months"]].rename(
        columns={"project_id": "current_project_id", "start": "project_since", "months": "project_residency_months"})
    cur_a = ai[ai["end"] >= today][["emp_id", "account_id", "start", "months"]].rename(
        columns={"account_id": "current_account_id", "start": "account_since", "months": "account_residency_months"})
    tot_a = ai.groupby(["emp_id", "account_id"])["months"].sum().reset_index().rename(
        columns={"account_id": "current_account_id", "months": "total_account_months"})
    served = al.groupby("emp_id").agg(accounts_served=("account_id", "nunique"), projects_served=("project_id", "nunique")).reset_index()
    last = al.sort_values("end").groupby("emp_id").tail(1)[["emp_id", "account_id", "project_id", "end"]].rename(
        columns={"account_id": "last_account_id", "project_id": "last_project_id", "end": "last_allocation_end"})

    # bench days in the last 12 months (time not covered by any allocation since max(doj, today-365))
    ov = islands(al, None, 0)
    emp["doj"] = pd.to_datetime(emp["date_of_joining"])
    emp["win_start"] = emp["doj"].clip(lower=today - pd.Timedelta(days=365))
    ov = ov.merge(emp[["emp_id", "win_start"]], on="emp_id")
    ov["cov"] = (ov["end"].clip(upper=today) - ov[["start", "win_start"]].max(axis=1)).dt.days.clip(lower=0)
    covered = ov.groupby("emp_id")["cov"].sum()
    emp["bench_days_12m"] = ((today - emp["win_start"]).dt.days - emp["emp_id"].map(covered).fillna(0)).clip(lower=0)

    # cool-off: account left recently → cannot be re-deployed there until end + cool_off_months
    lastisl = ai.sort_values("end").groupby(["emp_id", "account_id"]).tail(1).merge(
        al[["account_id", "cool"]].drop_duplicates(), on="account_id")
    lastisl = lastisl[lastisl["end"] < today].copy()
    lastisl["until"] = lastisl["end"] + pd.to_timedelta(lastisl["cool"] * MONTH, unit="D")
    lastisl = lastisl[lastisl["until"] > today]

    df = (emp.merge(cur, on="emp_id", how="left")
             .merge(cur_p, on=["emp_id", "current_project_id"], how="left")
             .merge(cur_a, on=["emp_id", "current_account_id"], how="left")
             .merge(tot_a, on=["emp_id", "current_account_id"], how="left")
             .merge(served, on="emp_id", how="left")
             .merge(last, on="emp_id", how="left"))
    cool_map = (lastisl.merge(df[["emp_id", "current_account_id"]], on="emp_id")
                .query("account_id != current_account_id")
                .assign(txt=lambda x: x["account_id"] + ":" + x["until"].dt.strftime("%Y-%m-%d"))
                .groupby("emp_id")["txt"].agg(";".join))
    df["cool_off_accounts"] = df["emp_id"].map(cool_map)

    allocated = df["status"].isin(["allocated", "notice"])
    for c in ("project_residency_months", "account_residency_months", "total_account_months"):
        df[c] = df[c].where(allocated).round(1)
    df["rotation_threshold_months"] = df["rotation_threshold_months"].fillna(config_threshold(conn))
    df["rotation_due"] = (allocated & (df["account_residency_months"] > df["rotation_threshold_months"])).astype(int)
    df["months_over_threshold"] = (df["account_residency_months"] - df["rotation_threshold_months"]).where(df["rotation_due"] == 1).round(1)
    df["days_to_rolloff"] = (df["planned_rolloff"] - today).dt.days.where(allocated)
    df["planned_rolloff"] = df["planned_rolloff"].where(allocated)
    on_bench = df["status"].isin(["bench", "training"])
    df["bench_aging_days"] = (today - pd.to_datetime(df["bench_since"])).dt.days.where(on_bench)
    df["residency_band"] = [band_for(s, f, m) for s, f, m in zip(df["status"], df["function"], df["account_residency_months"])]
    df["release_requires_approval"] = (df["status"] == "allocated").astype(int)
    df["availability_date"] = df["planned_rolloff"].where(df["status"] == "allocated")
    df.loc[on_bench, "availability_date"] = today
    df.loc[df["function"] != "Delivery", "bench_days_12m"] = None
    df["as_of"] = today.strftime("%Y-%m-%d")

    cols = ["emp_id", "status", "current_project_id", "current_account_id", "project_since", "project_residency_months",
            "account_since", "account_residency_months", "total_account_months", "rotation_threshold_months", "rotation_due",
            "months_over_threshold", "planned_rolloff", "days_to_rolloff", "availability_date", "release_requires_approval",
            "bench_aging_days", "bench_days_12m", "residency_band", "accounts_served", "projects_served",
            "last_account_id", "last_project_id", "last_allocation_end", "cool_off_accounts", "as_of"]
    out = df[cols].copy()
    for c in ("project_since", "account_since", "planned_rolloff", "availability_date", "last_allocation_end"):
        out[c] = pd.to_datetime(out[c]).dt.strftime("%Y-%m-%d")
    return out


def config_threshold(conn) -> int:
    return scalar(conn, "SELECT max(rotation_threshold_months) FROM accounts") or 24


def refresh(conn, as_of=None) -> dict:
    df = compute_residency(conn, as_of)
    df.to_sql("residency_snapshot", conn, if_exists="replace", index=False)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_res_emp ON residency_snapshot(emp_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_res_acc ON residency_snapshot(current_account_id)")
    conn.commit()
    return {"rows": len(df), "as_of": df["as_of"].iloc[0], "rotation_due": int(df["rotation_due"].sum())}


# ------------------------------------------------------------------ queries
def _filters(ibu_id=None, account_id=None, role_family=None):
    where, params = ["1=1"], []
    if ibu_id:
        where.append("e.ibu_id = ?"); params.append(ibu_id)
    if account_id:
        where.append("r.current_account_id = ?"); params.append(account_id)
    if role_family:
        where.append("e.role_family = ?"); params.append(role_family)
    return " AND ".join(where), params


def summary(conn, ibu_id=None, account_id=None, role_family=None) -> dict:
    w, p = _filters(ibu_id, account_id, role_family)
    base = f"FROM residency_snapshot r JOIN employees e USING(emp_id) WHERE {w}"
    k = one(conn, f"""SELECT count(*) headcount,
        sum(e.function='Delivery') delivery, sum(e.status='allocated') allocated, sum(e.status='bench') bench,
        sum(e.status='training') training, sum(e.status='notice') notice, sum(r.rotation_due) rotation_due,
        sum(e.status='allocated' AND r.days_to_rolloff BETWEEN 0 AND 30) rolling_off_30,
        sum(e.status='allocated' AND r.days_to_rolloff BETWEEN 0 AND 60) rolling_off_60,
        round(avg(CASE WHEN e.status IN ('bench','training') THEN r.bench_aging_days END), 1) avg_bench_aging_days,
        round(avg(CASE WHEN e.status='allocated' THEN r.account_residency_months END), 1) avg_account_residency_months,
        max(r.as_of) as_of {base}""", p)
    k = {key: (v or 0) if key != "as_of" else v for key, v in k.items()}
    k["bench_pct"] = round(100 * (k["bench"] + k["training"]) / k["delivery"], 1) if k["delivery"] else 0.0

    bands = {b["band"]: b["n"] for b in rows(conn, f"SELECT r.residency_band band, count(*) n {base} GROUP BY 1", p)}
    k["residency_bands"] = [{"band": b, "employees": bands.get(b, 0)} for b in BAND_ORDER if b != "N/A"]
    aging = {a["b"]: a["n"] for a in rows(conn, f"""SELECT CASE WHEN r.bench_aging_days<=15 THEN '0-15d'
        WHEN r.bench_aging_days<=30 THEN '16-30d' WHEN r.bench_aging_days<=60 THEN '31-60d'
        WHEN r.bench_aging_days<=90 THEN '61-90d' ELSE '90d+' END b, count(*) n {base}
        AND e.status IN ('bench','training') GROUP BY 1""", p)}
    k["bench_aging"] = [{"bucket": b, "employees": aging.get(b, 0)} for b in AGING_BUCKETS]
    k["by_ibu"] = rows(conn, f"""SELECT e.ibu_id, i.name ibu_name, count(*) headcount,
        sum(e.status='allocated') allocated, sum(e.status IN ('bench','training')) bench,
        round(100.0*sum(e.status IN ('bench','training'))/max(sum(e.function='Delivery'),1),1) bench_pct,
        sum(r.rotation_due) rotation_due,
        round(avg(CASE WHEN e.status='allocated' THEN r.account_residency_months END),1) avg_account_residency_months
        {base.replace('WHERE', 'JOIN ibus i ON i.ibu_id=e.ibu_id WHERE')} GROUP BY e.ibu_id ORDER BY headcount DESC""", p)
    k["top_rotation_accounts"] = rows(conn, f"""SELECT r.current_account_id account_id, a.name account_name, a.ibu_id,
        count(*) allocated, sum(r.rotation_due) rotation_due
        {base.replace('WHERE', 'JOIN accounts a ON a.account_id=r.current_account_id WHERE')}
        AND e.status='allocated' GROUP BY 1 HAVING sum(r.rotation_due)>0 ORDER BY rotation_due DESC LIMIT 10""", p)
    return k


def heatmap(conn, ibu_id=None) -> list[dict]:
    w, p = _filters(ibu_id)
    data = rows(conn, f"""SELECT r.current_account_id account_id, a.name account_name, a.ibu_id,
                         r.residency_band band, count(*) n, sum(r.rotation_due) due,
                         sum(r.account_residency_months) msum
                  FROM residency_snapshot r JOIN employees e USING(emp_id)
                  JOIN accounts a ON a.account_id = r.current_account_id
                  WHERE {w} AND e.status IN ('allocated','notice') GROUP BY 1,2,3,4""", p)
    acc: dict[str, dict] = {}
    for d in data:
        a = acc.setdefault(d["account_id"], {"account_id": d["account_id"], "account_name": d["account_name"],
                                             "ibu_id": d["ibu_id"], "headcount": 0, "rotation_due": 0, "_msum": 0.0,
                                             "bands": {b: 0 for b in BAND_ORDER[:5]}})
        a["bands"][d["band"]] = d["n"]
        a["headcount"] += d["n"]
        a["rotation_due"] += d["due"] or 0
        a["_msum"] += d["msum"] or 0
    out = []
    for a in acc.values():
        a["avg_months"] = round(a.pop("_msum") / a["headcount"], 1) if a["headcount"] else 0
        a["rotation_due_pct"] = round(100 * a["rotation_due"] / a["headcount"], 1) if a["headcount"] else 0
        out.append(a)
    return sorted(out, key=lambda a: -a["headcount"])


SORTS = {"residency": "r.account_residency_months DESC", "rolloff": "r.days_to_rolloff ASC",
         "bench_aging": "r.bench_aging_days DESC", "name": "e.first_name, e.last_name"}


def list_employees(conn, ibu_id=None, account_id=None, band=None, rotation_due=None, status=None,
                   q=None, sort="residency", page=1, page_size=50) -> dict:
    w, p = _filters(ibu_id, account_id)
    if band:
        w += " AND r.residency_band = ?"; p.append(band)
    if rotation_due is not None:
        w += " AND r.rotation_due = ?"; p.append(int(rotation_due))
    if status:
        w += " AND e.status = ?"; p.append(status)
    if q:
        w += " AND (e.first_name || ' ' || e.last_name LIKE ? OR e.emp_id LIKE ? OR e.email LIKE ?)"; p += [f"%{q}%"] * 3
    base = f"""FROM residency_snapshot r JOIN employees e USING(emp_id)
               LEFT JOIN projects pr ON pr.project_id = r.current_project_id
               LEFT JOIN accounts a ON a.account_id = r.current_account_id
               WHERE {w} AND e.function='Delivery'"""
    total = scalar(conn, f"SELECT count(*) {base}", p)
    page_size = max(1, min(int(page_size), 200))
    items = rows(conn, f"""SELECT e.emp_id, e.first_name || ' ' || e.last_name name, e.designation, e.grade, e.ibu_id,
        e.role_family_name, e.base_location, e.status, r.residency_band, r.current_project_id, pr.name project_name,
        r.current_account_id, a.name account_name, r.project_residency_months, r.account_residency_months,
        r.total_account_months, r.rotation_threshold_months, r.rotation_due, r.months_over_threshold,
        r.planned_rolloff, r.days_to_rolloff, r.release_requires_approval, r.bench_aging_days
        {base} ORDER BY {SORTS.get(sort, SORTS['residency'])} NULLS LAST, e.emp_id
        LIMIT ? OFFSET ?""", p + [page_size, (max(1, int(page)) - 1) * page_size])
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def employee_residency(conn, emp_id: str) -> dict:
    snap = one(conn, "SELECT * FROM residency_snapshot WHERE emp_id=?", (emp_id,))
    if not snap:
        raise NotFound(f"Employee {emp_id} not found")
    al = pd.read_sql("""SELECT a.emp_id, a.project_id, a.account_id, a.start_date, a.end_date, ac.name account_name,
                               p.name project_name FROM allocations a JOIN accounts ac USING(account_id)
                               JOIN projects p ON p.project_id=a.project_id WHERE a.emp_id=?""", conn, params=(emp_id,))
    history = []
    if len(al):
        today = pd.Timestamp(snap["as_of"])
        al["start"] = pd.to_datetime(al["start_date"])
        al = al[al["start"] <= today].copy()
        al["end"] = pd.to_datetime(al["end_date"]).clip(upper=today)
        names = al.drop_duplicates("account_id").set_index("account_id")["account_name"]
        for r in islands(al, "account_id", config.RESIDENCY_GAP_DAYS).sort_values("start").itertuples():
            history.append({"account_id": r.account_id, "account_name": names[r.account_id],
                            "start": r.start.strftime("%Y-%m-%d"), "end": r.end.strftime("%Y-%m-%d"),
                            "months": round(r.months, 1), "stints": int(r.stints), "current": bool(r.end >= today)})
    cool = []
    if snap.get("cool_off_accounts"):
        for part in snap["cool_off_accounts"].split(";"):
            acc, until = part.split(":")
            cool.append({"account_id": acc, "until": until,
                         "account_name": scalar(conn, "SELECT name FROM accounts WHERE account_id=?", (acc,))})
    snap["cool_off"] = cool
    snap["account_history"] = history
    return snap

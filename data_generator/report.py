"""Sanity report for the generated dataset (writes <out>/data_report.md)."""
import os
import sqlite3

import pandas as pd

from . import config as C


def _md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def account_residency(alloc: pd.DataFrame, gap_days: int = 30) -> pd.DataFrame:
    """Gaps-and-islands: merge consecutive stints on the same account when the gap <= gap_days."""
    a = alloc[["emp_id", "account_id", "start_date", "end_date"]].copy()
    a["start_date"] = pd.to_datetime(a["start_date"])
    a["end_date"] = pd.to_datetime(a["end_date"]).clip(upper=pd.Timestamp(C.TODAY))
    a = a.sort_values(["emp_id", "account_id", "start_date"])
    prev_end = a.groupby(["emp_id", "account_id"])["end_date"].shift()
    a["new_island"] = ((a["start_date"] - prev_end).dt.days > gap_days) | prev_end.isna()
    a["island"] = a.groupby(["emp_id", "account_id"])["new_island"].cumsum()
    isl = a.groupby(["emp_id", "account_id", "island"]).agg(start=("start_date", "min"), end=("end_date", "max")).reset_index()
    isl["months"] = (isl["end"] - isl["start"]).dt.days / 30.44
    return isl


def build_report(out_dir: str) -> str:
    con = sqlite3.connect(os.path.join(out_dir, "coforce.db"))
    q = lambda s: pd.read_sql(s, con)
    emp = q("select * from employees")
    alloc = q("select * from allocations")
    out = [f"# {C.COMPANY} synthetic data — sanity report (as of {C.TODAY})\n"]

    counts = pd.DataFrame([(t, q(f"select count(*) n from {t}")["n"][0]) for t in
                           q("select name from sqlite_master where type='table' order by name")["name"]],
                          columns=["table", "rows"])
    out += ["## Table sizes", _md(counts), ""]

    out += ["## Workforce by status", _md(emp["status"].value_counts().rename_axis("status").reset_index(name="employees")), ""]
    dl = emp[emp.function == "Delivery"]
    out.append(f"Bench share (delivery, incl. training): **{dl.status.isin(['bench','training']).mean():.1%}**\n")

    ibu = emp.groupby("ibu_id").agg(employees=("emp_id", "count"),
                                    bench=("status", lambda s: int(s.isin(["bench", "training"]).sum()))).reset_index()
    out += ["## Employees by IBU", _md(ibu), ""]
    out += ["## Grade pyramid", _md(emp["grade"].value_counts().sort_index().rename_axis("grade").reset_index(name="employees")), ""]
    out += ["## Role families (delivery)", _md(dl["role_family_name"].value_counts().rename_axis("role_family").reset_index(name="employees")), ""]

    bench = dl[dl.status.isin(["bench", "training"])].copy()
    bench["aging_days"] = (pd.Timestamp(C.TODAY) - pd.to_datetime(bench.bench_since)).dt.days
    out += ["## Bench aging (days)", _md(bench["aging_days"].describe().round(1).rename_axis("stat").reset_index()), ""]

    isl = account_residency(alloc)
    cur = isl[isl["end"] >= pd.Timestamp(C.TODAY)]
    out.append("## Current account residency (allocated employees)")
    out.append(_md(pd.cut(cur["months"], [0, 12, 18, 24, 36, 60, 200],
                          labels=["<12m", "12-18m", "18-24m", "24-36m", "36-60m", "60m+"]).value_counts().sort_index()
                   .rename_axis("residency").reset_index(name="employees")))
    out.append(f"\nPast {C.ROTATION_THRESHOLD_MONTHS}-month rotation threshold: **{(cur.months > C.ROTATION_THRESHOLD_MONTHS).sum()}** employees\n")

    a = alloc.copy()
    a["end_date"] = pd.to_datetime(a["end_date"])
    rolling = a[(a.end_date > pd.Timestamp(C.TODAY)) & (a.end_date <= pd.Timestamp(C.TODAY) + pd.Timedelta(days=60))]
    out.append(f"Rolling off in the next 60 days: **{rolling.emp_id.nunique()}** employees\n")

    es = q("select * from employee_skills")
    out += ["## Skills per employee", _md(es.groupby("emp_id").size().describe().round(1).rename_axis("stat").reset_index(name="skills")), ""]
    out += ["## Skill sources", _md(es["source"].value_counts().rename_axis("source").reset_index(name="rows")), ""]
    top = es.merge(q("select skill_id, name from skills"), on="skill_id")["name"].value_counts().head(20)
    out += ["## Top 20 skills", _md(top.rename_axis("skill").reset_index(name="employees")), ""]
    dm = q("select status, count(*) demands, sum(positions) positions from demands group by status")
    out += ["## Demands", _md(dm), ""]

    path = os.path.join(out_dir, "data_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    con.close()
    return path

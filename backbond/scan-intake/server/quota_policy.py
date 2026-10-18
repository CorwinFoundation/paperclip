"""One allowance predicate shared by intake and the partner-code manager."""
def quota_counted_sql(alias="", refund_terminal_failures=True):
    p = alias + "." if alias else ""
    sql = (f"{p}status NOT IN ('connection_failed','cancelled','interrupted') "
           f"AND NOT ({p}status='launch_failed' AND {p}scanner_id IS NULL)")
    if refund_terminal_failures:
        sql += f" AND NOT ({p}status='finished' AND COALESCE({p}outcome,'')='failed')"
    return sql

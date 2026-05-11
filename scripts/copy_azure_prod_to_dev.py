import argparse
import os
from collections import defaultdict, deque
from datetime import datetime, timezone

import psycopg
from psycopg import sql


SOURCE_PREFIX = "SOURCE_AZURE_DATABASE"
DEST_PREFIX = "AZURE_DATABASE"


def env(prefix, name):
    value = os.getenv(f"{prefix}_{name}")
    if not value:
        raise RuntimeError(f"Missing {prefix}_{name}")
    return value


def connect(prefix):
    return psycopg.connect(
        host=env(prefix, "HOST"),
        port=env(prefix, "PORT"),
        dbname=env(prefix, "NAME"),
        user=env(prefix, "USER"),
        password=env(prefix, "PASSWORD"),
        sslmode="require",
        connect_timeout=20,
    )


def quote_table(table):
    return sql.Identifier("public", table)


def table_list(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            select tablename
            from pg_tables
            where schemaname = 'public'
            order by tablename
            """
        )
        return [row[0] for row in cur.fetchall()]


def table_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema = 'public' and table_name = %s
            order by ordinal_position
            """,
            (table,),
        )
        return [row[0] for row in cur.fetchall()]


def row_counts(conn, tables):
    counts = {}
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(sql.SQL("select count(*) from {}").format(quote_table(table)))
            counts[table] = cur.fetchone()[0]
    return counts


def dependency_order(conn, tables):
    table_set = set(tables)
    parents = defaultdict(set)
    children = defaultdict(set)
    with conn.cursor() as cur:
        cur.execute(
            """
            select src.relname as child, dst.relname as parent
            from pg_constraint con
            join pg_class src on src.oid = con.conrelid
            join pg_namespace src_ns on src_ns.oid = src.relnamespace
            join pg_class dst on dst.oid = con.confrelid
            join pg_namespace dst_ns on dst_ns.oid = dst.relnamespace
            where con.contype = 'f'
              and src_ns.nspname = 'public'
              and dst_ns.nspname = 'public'
            """
        )
        for child, parent in cur.fetchall():
            if child in table_set and parent in table_set and child != parent:
                parents[child].add(parent)
                children[parent].add(child)

    queue = deque(sorted([table for table in tables if not parents[table]]))
    ordered = []
    while queue:
        table = queue.popleft()
        ordered.append(table)
        for child in sorted(children[table]):
            parents[child].discard(table)
            if not parents[child]:
                queue.append(child)

    remaining = [table for table in tables if table not in ordered]
    return ordered + sorted(remaining)


def backup_dest(dest, tables):
    backup_schema = "backup_before_prod_copy_" + datetime.now(timezone.utc).strftime(
        "%Y%m%d_%H%M%S"
    )
    with dest.cursor() as cur:
        cur.execute(sql.SQL("create schema {}").format(sql.Identifier(backup_schema)))
        for table in tables:
            cur.execute(
                sql.SQL("create table {} as table {}").format(
                    sql.Identifier(backup_schema, table),
                    quote_table(table),
                )
            )
    return backup_schema


def truncate_dest(dest, tables):
    if not tables:
        return
    with dest.cursor() as cur:
        cur.execute(
            sql.SQL("truncate {} restart identity cascade").format(
                sql.SQL(", ").join(quote_table(table) for table in tables)
            )
        )


def copy_table(source, dest, table):
    columns = table_columns(source, table)
    column_sql = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
    table_sql = quote_table(table)
    copy_out = sql.SQL("copy (select {} from {}) to stdout").format(
        column_sql, table_sql
    )
    copy_in = sql.SQL("copy {} ({}) from stdin").format(table_sql, column_sql)
    rows = 0
    with source.cursor() as source_cur, dest.cursor() as dest_cur:
        with source_cur.copy(copy_out) as source_copy:
            with dest_cur.copy(copy_in) as dest_copy:
                for data in source_copy:
                    dest_copy.write(data)
                    rows += 1
    return rows


def reset_sequences(dest):
    with dest.cursor() as cur:
        cur.execute(
            """
            select seq_ns.nspname, seq.relname, tbl.relname, col.attname
            from pg_class seq
            join pg_namespace seq_ns on seq_ns.oid = seq.relnamespace
            join pg_depend dep on dep.objid = seq.oid and dep.deptype = 'a'
            join pg_class tbl on tbl.oid = dep.refobjid
            join pg_namespace tbl_ns on tbl_ns.oid = tbl.relnamespace
            join pg_attribute col on col.attrelid = tbl.oid and col.attnum = dep.refobjsubid
            where seq.relkind = 'S'
              and tbl_ns.nspname = 'public'
            """
        )
        sequences = cur.fetchall()
        for seq_schema, seq_name, table, column in sequences:
            cur.execute(
                sql.SQL(
                    "select setval({}, coalesce((select max({}) from {}), 1), "
                    "coalesce((select max({}) from {}), 0) > 0)"
                ).format(
                    sql.Literal(f"{seq_schema}.{seq_name}"),
                    sql.Identifier(column),
                    quote_table(table),
                    sql.Identifier(column),
                    quote_table(table),
                )
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    same_target = (
        env(SOURCE_PREFIX, "HOST") == env(DEST_PREFIX, "HOST")
        and env(SOURCE_PREFIX, "NAME") == env(DEST_PREFIX, "NAME")
        and env(SOURCE_PREFIX, "USER") == env(DEST_PREFIX, "USER")
    )
    if same_target:
        raise RuntimeError("Source and destination database targets are the same.")

    with connect(SOURCE_PREFIX) as source, connect(DEST_PREFIX) as dest:
        source_tables = table_list(source)
        dest_tables = table_list(dest)
        missing_in_dest = sorted(set(source_tables) - set(dest_tables))
        extra_in_dest = sorted(set(dest_tables) - set(source_tables))
        shared = sorted(set(source_tables) & set(dest_tables))
        ordered = dependency_order(source, shared)

        source_counts = row_counts(source, shared)
        dest_counts = row_counts(dest, shared)

        print(f"source_host={env(SOURCE_PREFIX, 'HOST')}")
        print(f"dest_host={env(DEST_PREFIX, 'HOST')}")
        print(f"shared_tables={len(shared)}")
        print(f"missing_in_dest={','.join(missing_in_dest) if missing_in_dest else 'none'}")
        print(f"extra_in_dest={','.join(extra_in_dest) if extra_in_dest else 'none'}")
        print("table,source_rows,dest_rows")
        for table in ordered:
            print(f"{table},{source_counts[table]},{dest_counts[table]}")

        if not args.execute:
            print("dry_run=true")
            return

        backup_schema = backup_dest(dest, dest_tables)
        print(f"backup_schema={backup_schema}")
        truncate_dest(dest, shared)
        for table in ordered:
            rows = copy_table(source, dest, table)
            print(f"copied {table} rows={rows}")
        reset_sequences(dest)
        dest.commit()
        print("copy_complete=true")


if __name__ == "__main__":
    main()

from __future__ import annotations

import ast
import html
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
OUT = ROOT / "docs" / "VETCODE_APPLICATION_SIZE_SUMMARY.html"

SOURCE_EXTENSIONS = {
    ".py": "Python backend / scripts",
    ".html": "HTML pages / components",
    ".js": "JavaScript browser logic",
    ".css": "CSS domain styling",
    ".svg": "SVG visual assets",
    ".txt": "Prompt / text fixtures",
    ".md": "Markdown documentation",
}

SOURCE_ROOTS = [
    ROOT / "backend",
    ROOT / "scripts",
    ROOT / "frontend",
    ROOT / "ui",
    ROOT / "docs",
]

EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    "exports",
    "uploads",
    "calendar_tokens",
    "qa-artifacts",
}

RUNTIME_DATA_DIRS = [
    BACKEND / "data",
    ROOT / "data",
]


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & EXCLUDED_PARTS)


def source_files() -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path in seen or is_excluded(path):
                continue
            if path.suffix.lower() in SOURCE_EXTENSIONS:
                files.append(path)
                seen.add(path)
    for path in [ROOT / "README.txt", ROOT / "requirements.txt", ROOT / "navigation.js", ROOT / "views.html"]:
        if path.exists() and path not in seen:
            files.append(path)
    return sorted(files, key=lambda p: rel(p).lower())


def count_lines(path: Path) -> tuple[int, int]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0, 0
    lines = text.splitlines()
    nonblank = sum(1 for line in lines if line.strip())
    return len(lines), nonblank


def source_summary(files: list[Path]) -> dict:
    by_ext: dict[str, dict] = defaultdict(lambda: {"files": 0, "lines": 0, "nonblank": 0, "bytes": 0})
    by_root: dict[str, dict] = defaultdict(lambda: {"files": 0, "lines": 0, "nonblank": 0})
    totals = {"files": 0, "lines": 0, "nonblank": 0, "bytes": 0}
    largest: list[dict] = []

    for path in files:
        ext = path.suffix.lower() or "(none)"
        lines, nonblank = count_lines(path)
        size = path.stat().st_size
        root_name = path.relative_to(ROOT).parts[0] if path.is_relative_to(ROOT) else "external"

        by_ext[ext]["files"] += 1
        by_ext[ext]["lines"] += lines
        by_ext[ext]["nonblank"] += nonblank
        by_ext[ext]["bytes"] += size

        by_root[root_name]["files"] += 1
        by_root[root_name]["lines"] += lines
        by_root[root_name]["nonblank"] += nonblank

        totals["files"] += 1
        totals["lines"] += lines
        totals["nonblank"] += nonblank
        totals["bytes"] += size
        largest.append({"path": rel(path), "ext": ext, "lines": lines, "nonblank": nonblank, "bytes": size})

    largest.sort(key=lambda item: item["lines"], reverse=True)
    return {
        "totals": totals,
        "by_ext": dict(sorted(by_ext.items())),
        "by_root": dict(sorted(by_root.items())),
        "largest": largest[:20],
    }


def count_python_symbols(files: list[Path]) -> dict:
    stats = {"modules": 0, "classes": 0, "functions": 0, "async_functions": 0}
    per_file: list[dict] = []
    for path in files:
        if path.suffix.lower() != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        classes = sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree))
        funcs = sum(isinstance(node, ast.FunctionDef) for node in ast.walk(tree))
        async_funcs = sum(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))
        stats["modules"] += 1
        stats["classes"] += classes
        stats["functions"] += funcs
        stats["async_functions"] += async_funcs
        per_file.append({"path": rel(path), "classes": classes, "functions": funcs, "async_functions": async_funcs})
    per_file.sort(key=lambda item: item["classes"] + item["functions"] + item["async_functions"], reverse=True)
    stats["largest"] = per_file[:15]
    return stats


def count_frontend_surfaces(files: list[Path]) -> dict:
    html_files = [path for path in files if path.suffix.lower() == ".html"]
    js_files = [path for path in files if path.suffix.lower() == ".js"]
    css_files = [path for path in files if path.suffix.lower() == ".css"]
    page_files = [path for path in html_files if "backend/ui/pages" in rel(path) and "components/" not in rel(path)]
    component_files = [path for path in html_files if "backend/ui/pages/components" in rel(path)]
    return {
        "html": len(html_files),
        "js": len(js_files),
        "css": len(css_files),
        "ui_pages": len(page_files),
        "ui_components": len(component_files),
    }


def count_fastapi_routes() -> dict:
    route_re = re.compile(r"@(app|router)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)")
    routes = []
    for path in [BACKEND / "main.py", *sorted((BACKEND / "azureUtils" / "routes").glob("*.py"))]:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in route_re.finditer(text):
            routes.append({"file": rel(path), "method": match.group(2).upper(), "path": match.group(3)})
    by_method = Counter(route["method"] for route in routes)
    return {"total": len(routes), "by_method": dict(sorted(by_method.items())), "sample": routes[:30]}


def sqlite_inventory() -> list[dict]:
    dbs = []
    for path in sorted(BACKEND.glob("*.db")) + sorted(ROOT.glob("*.db")):
        if is_excluded(path):
            continue
        info = {"path": rel(path), "bytes": path.stat().st_size, "tables": 0, "columns": 0, "table_details": []}
        try:
            with sqlite3.connect(path) as conn:
                tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                info["tables"] = len(tables)
                for table in tables:
                    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
                    info["columns"] += len(cols)
                    try:
                        rows = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    except Exception:
                        rows = None
                    info["table_details"].append({"table": table, "columns": len(cols), "rows": rows})
        except Exception as exc:
            info["error"] = str(exc)
        dbs.append(info)
    return dbs


def json_field_inventory() -> list[dict]:
    stores = []
    for base in RUNTIME_DATA_DIRS:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.json")):
            if is_excluded(path):
                continue
            info = {"path": rel(path), "bytes": path.stat().st_size, "records": 0, "fields": 0, "top_fields": []}
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
                keys = Counter()

                def walk(value):
                    if isinstance(value, dict):
                        for key, child in value.items():
                            keys[str(key)] += 1
                            walk(child)
                    elif isinstance(value, list):
                        for child in value:
                            walk(child)

                walk(data)
                if isinstance(data, list):
                    info["records"] = len(data)
                elif isinstance(data, dict):
                    info["records"] = len(data)
                info["fields"] = len(keys)
                info["top_fields"] = [key for key, _ in keys.most_common(10)]
            except Exception as exc:
                info["error"] = str(exc)
            stores.append(info)
    return stores


def postgres_inventory() -> dict:
    env_names = [
        "AZURE_DATABASE_HOST",
        "AZURE_DATABASE_NAME",
        "AZURE_DATABASE_USER",
        "AZURE_DATABASE_PASSWORD",
        "AZURE_DATABASE_PORT",
    ]
    env_present = {name: bool(os.getenv(name, "").strip()) for name in env_names}
    if not all(env_present.values()):
        try:
            from dotenv import load_dotenv

            load_dotenv(BACKEND / ".env")
            env_present = {name: bool(os.getenv(name, "").strip()) for name in env_names}
        except Exception:
            pass
    result = {
        "configured": all(env_present.values()),
        "env_present": env_present,
        "tables": 0,
        "columns": 0,
        "table_details": [],
        "note": "",
    }
    if not result["configured"]:
        result["note"] = "Azure PostgreSQL environment variables were not complete when generated."
        return result

    try:
        import psycopg

        with psycopg.connect(
            host=os.getenv("AZURE_DATABASE_HOST"),
            dbname=os.getenv("AZURE_DATABASE_NAME"),
            user=os.getenv("AZURE_DATABASE_USER"),
            password=os.getenv("AZURE_DATABASE_PASSWORD"),
            port=int(os.getenv("AZURE_DATABASE_PORT", "5432")),
            sslmode="require",
            connect_timeout=8,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name, COUNT(*)::int
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    GROUP BY table_name
                    ORDER BY table_name
                    """
                )
                rows = cur.fetchall()
        result["tables"] = len(rows)
        result["columns"] = sum(int(row[1]) for row in rows)
        result["table_details"] = [{"table": row[0], "columns": int(row[1])} for row in rows]
    except Exception as exc:
        result["note"] = f"Could not query Azure PostgreSQL schema: {exc.__class__.__name__}"
    return result


def storage_totals(sqlite_dbs: list[dict], json_stores: list[dict], pg: dict) -> dict:
    sqlite_tables = sum(item.get("tables", 0) for item in sqlite_dbs)
    sqlite_columns = sum(item.get("columns", 0) for item in sqlite_dbs)
    json_fields = sum(item.get("fields", 0) for item in json_stores)
    return {
        "database_count": (1 if pg.get("configured") else 0) + len(sqlite_dbs) + len(json_stores),
        "postgres_tables": pg.get("tables", 0),
        "postgres_columns": pg.get("columns", 0),
        "sqlite_dbs": len(sqlite_dbs),
        "sqlite_tables": sqlite_tables,
        "sqlite_columns": sqlite_columns,
        "json_stores": len(json_stores),
        "json_fields": json_fields,
        "total_tables_or_stores": pg.get("tables", 0) + sqlite_tables + len(json_stores),
        "total_columns_or_fields": pg.get("columns", 0) + sqlite_columns + json_fields,
    }


def table(rows: list[list[object]], headers: list[str]) -> str:
    head = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = "\n".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def code_type_html(summary: dict) -> str:
    rows = []
    for ext, item in sorted(summary["by_ext"].items(), key=lambda pair: pair[1]["lines"], reverse=True):
        rows.append([
            f"<code>{esc(ext)}</code>",
            esc(SOURCE_EXTENSIONS.get(ext, "Application file")),
            f"{item['files']:,}",
            f"{item['lines']:,}",
            f"{item['nonblank']:,}",
            f"{item['bytes'] / 1024:,.1f} KB",
        ])
    return table(rows, ["Type", "Meaning", "Files", "Total LOC", "Nonblank LOC", "Size"])


def root_html(summary: dict) -> str:
    rows = []
    for root, item in summary["by_root"].items():
        rows.append([esc(root), f"{item['files']:,}", f"{item['lines']:,}", f"{item['nonblank']:,}"])
    return table(rows, ["Area", "Files", "Total LOC", "Nonblank LOC"])


def largest_html(summary: dict) -> str:
    rows = []
    for item in summary["largest"]:
        rows.append([f"<code>{esc(item['path'])}</code>", f"<code>{esc(item['ext'])}</code>", f"{item['lines']:,}", f"{item['nonblank']:,}"])
    return table(rows, ["File", "Type", "Total LOC", "Nonblank LOC"])


def sqlite_html(sqlite_dbs: list[dict]) -> str:
    rows = []
    for item in sqlite_dbs:
        rows.append([
            f"<code>{esc(item['path'])}</code>",
            f"{item.get('tables', 0):,}",
            f"{item.get('columns', 0):,}",
            f"{item.get('bytes', 0) / 1024:,.1f} KB",
            esc(item.get("error", "")),
        ])
    return table(rows, ["SQLite DB", "Tables", "Columns", "Size", "Note"])


def json_html(json_stores: list[dict]) -> str:
    rows = []
    for item in sorted(json_stores, key=lambda x: x.get("bytes", 0), reverse=True):
        rows.append([
            f"<code>{esc(item['path'])}</code>",
            f"{item.get('records', 0):,}",
            f"{item.get('fields', 0):,}",
            f"{item.get('bytes', 0) / 1024:,.1f} KB",
            esc(", ".join(item.get("top_fields", [])[:6])),
        ])
    return table(rows, ["JSON Store", "Top-level Records", "Distinct Field Names", "Size", "Common Fields"])


def postgres_html(pg: dict) -> str:
    note = pg.get("note") or ("Connected and counted schema metadata only." if pg.get("configured") else "")
    rows = [
        ["Azure PostgreSQL", "Yes" if pg.get("configured") else "No", f"{pg.get('tables', 0):,}", f"{pg.get('columns', 0):,}", esc(note)],
    ]
    details = table(rows, ["Database", "Configured", "Tables", "Columns", "Note"])
    if pg.get("table_details"):
        top = sorted(pg["table_details"], key=lambda item: item["columns"], reverse=True)[:20]
        details += table([[esc(item["table"]), f"{item['columns']:,}"] for item in top], ["Largest PostgreSQL Tables by Column Count", "Columns"])
    return details


def routes_html(routes: dict) -> str:
    method_summary = ", ".join(f"{method}: {count}" for method, count in routes["by_method"].items()) or "None"
    rows = [[f"{routes['total']:,}", esc(method_summary)]]
    html_out = table(rows, ["FastAPI Route Count", "By Method"])
    html_out += table(
        [[f"<code>{esc(route['method'])}</code>", f"<code>{esc(route['path'])}</code>", f"<code>{esc(route['file'])}</code>"] for route in routes["sample"]],
        ["Method", "Route", "File"],
    )
    return html_out


def python_html(py_stats: dict) -> str:
    summary = table(
        [[f"{py_stats['modules']:,}", f"{py_stats['classes']:,}", f"{py_stats['functions']:,}", f"{py_stats['async_functions']:,}"]],
        ["Python Modules", "Classes", "Functions", "Async Functions"],
    )
    rows = []
    for item in py_stats["largest"]:
        rows.append([f"<code>{esc(item['path'])}</code>", f"{item['classes']:,}", f"{item['functions']:,}", f"{item['async_functions']:,}"])
    return summary + table(rows, ["Python File", "Classes", "Functions", "Async Functions"])


def build() -> str:
    files = source_files()
    summary = source_summary(files)
    py_stats = count_python_symbols(files)
    frontend = count_frontend_surfaces(files)
    routes = count_fastapi_routes()
    sqlite_dbs = sqlite_inventory()
    json_stores = json_field_inventory()
    pg = postgres_inventory()
    storage = storage_totals(sqlite_dbs, json_stores, pg)
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>VETCODE Application Size Summary</title>
  <style>
    :root{{--bg:#f3f7f5;--paper:#fff;--ink:#102018;--muted:#5d6b63;--line:#d9e6df;--green:#2f7d4b;--blue:#145db2;--gold:#b88727;--code:#edf4f0}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,Helvetica,sans-serif;line-height:1.5}}
    .shell{{max-width:1320px;margin:0 auto;padding:28px 18px 72px}} .hero{{border:1px solid var(--line);border-radius:16px;padding:30px;background:linear-gradient(135deg,#fff,#eef8f0 55%,#edf3ff);box-shadow:0 18px 48px rgba(16,32,24,.08)}}
    h1,h2,h3{{margin:0;line-height:1.15}} h1{{font-size:36px}} h2{{margin-top:30px;padding-top:18px;border-top:1px solid var(--line);font-size:24px}} h3{{font-size:17px}} p{{margin:8px 0 0}} .muted{{color:var(--muted)}}
    .metric-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:18px}} .metric{{border:1px solid var(--line);border-radius:14px;padding:14px;background:#fff}} .metric strong{{display:block;font-size:30px;color:var(--green);line-height:1}} .metric span{{display:block;margin-top:6px;color:var(--muted);font-size:13px}}
    .card{{margin-top:14px;border:1px solid var(--line);border-radius:14px;padding:16px;background:var(--paper);box-shadow:0 10px 26px rgba(16,32,24,.05)}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}}
    table{{display:block;width:100%;margin-top:12px;border-collapse:collapse;overflow-x:auto;font-size:13px}} th,td{{border:1px solid var(--line);padding:9px 10px;text-align:left;vertical-align:top}} th{{background:#edf7ef;color:#183c24}} code{{border:1px solid #d8e4dc;border-radius:5px;padding:1px 5px;background:var(--code);font-family:Consolas,'Courier New',monospace;font-size:.92em}}
    .pill{{display:inline-block;margin:4px 5px 0 0;border:1px solid #cfe4d5;border-radius:999px;padding:4px 9px;background:#edf8ef;color:var(--green);font-size:12px;font-weight:800}} .pill.blue{{border-color:#c8d9f5;background:#edf5ff;color:var(--blue)}} .pill.gold{{border-color:#efd48d;background:#fff7e3;color:#7a550a}}
    .callout{{margin-top:14px;border-left:5px solid var(--blue);border-radius:10px;padding:13px 14px;background:#edf5ff}} .toc{{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}} .toc a{{border:1px solid var(--line);border-radius:999px;padding:8px 11px;background:#fff;color:var(--green);font-weight:800;text-decoration:none;font-size:13px}}
    @media(max-width:720px){{.hero{{padding:22px}} h1{{font-size:30px}}}}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <span class="pill gold">Application Size Sheet</span><span class="pill blue">Generated {esc(updated)}</span>
      <h1>VETCODE Application Size Summary</h1>
      <p class="muted">Counts app-owned source files and runtime data stores while excluding libraries, virtual environments, caches, uploaded resumes, and build artifacts. Regenerate with <code>python docs/generate_application_summary.py</code>.</p>
      <div class="toc">
        <a href="#source">Source Code</a><a href="#databases">Databases and Fields</a><a href="#routes">API Routes</a><a href="#frontend">Frontend</a><a href="#largest">Largest Files</a><a href="#notes">Counting Rules</a>
      </div>
      <div class="metric-grid">
        <div class="metric"><strong>{summary['totals']['files']:,}</strong><span>developed/source files counted</span></div>
        <div class="metric"><strong>{summary['totals']['lines']:,}</strong><span>total lines of source</span></div>
        <div class="metric"><strong>{summary['totals']['nonblank']:,}</strong><span>nonblank source lines</span></div>
        <div class="metric"><strong>{storage['database_count']:,}</strong><span>database-like stores counted</span></div>
        <div class="metric"><strong>{storage['total_tables_or_stores']:,}</strong><span>tables plus JSON stores</span></div>
        <div class="metric"><strong>{storage['total_columns_or_fields']:,}</strong><span>columns plus JSON field names</span></div>
      </div>
    </section>

    <section id="source">
      <h2>Source Code Inventory</h2>
      <div class="card">
        <p>App-owned files are grouped by extension and purpose. Runtime data and uploaded documents are not counted as source LOC.</p>
        {code_type_html(summary)}
      </div>
      <div class="card">
        <h3>By Application Area</h3>
        {root_html(summary)}
      </div>
      <div class="card">
        <h3>Python Structure</h3>
        {python_html(py_stats)}
      </div>
    </section>

    <section id="databases">
      <h2>Databases, Columns, and Fields</h2>
      <div class="metric-grid">
        <div class="metric"><strong>{pg.get('tables', 0):,}</strong><span>Azure PostgreSQL tables counted</span></div>
        <div class="metric"><strong>{pg.get('columns', 0):,}</strong><span>Azure PostgreSQL columns counted</span></div>
        <div class="metric"><strong>{storage['sqlite_dbs']:,}</strong><span>local SQLite DB files</span></div>
        <div class="metric"><strong>{storage['sqlite_columns']:,}</strong><span>local SQLite columns</span></div>
        <div class="metric"><strong>{storage['json_stores']:,}</strong><span>JSON operational stores</span></div>
        <div class="metric"><strong>{storage['json_fields']:,}</strong><span>distinct JSON field names</span></div>
      </div>
      <div class="card"><h3>Azure PostgreSQL</h3>{postgres_html(pg)}</div>
      <div class="card"><h3>Local SQLite Databases</h3>{sqlite_html(sqlite_dbs)}</div>
      <div class="card"><h3>JSON Runtime Stores</h3>{json_html(json_stores)}</div>
    </section>

    <section id="routes">
      <h2>Backend API Surface</h2>
      <div class="card">
        <p>FastAPI routes discovered from <code>backend/main.py</code> and <code>backend/azureUtils/routes/*.py</code>.</p>
        {routes_html(routes)}
      </div>
    </section>

    <section id="frontend">
      <h2>Frontend Surface</h2>
      <div class="card">
        {table([[
          f"{frontend['ui_pages']:,}",
          f"{frontend['ui_components']:,}",
          f"{frontend['html']:,}",
          f"{frontend['js']:,}",
          f"{frontend['css']:,}",
        ]], ["Backend UI Pages", "Backend UI Components", "HTML Files", "JS Files", "CSS Files"])}
      </div>
    </section>

    <section id="largest">
      <h2>Largest Source Files</h2>
      <div class="card">{largest_html(summary)}</div>
    </section>

    <section id="notes">
      <h2>Counting Rules</h2>
      <div class="grid">
        <div class="card"><h3>Included</h3><p><code>.py</code>, <code>.html</code>, <code>.js</code>, <code>.css</code>, <code>.svg</code>, <code>.txt</code>, and <code>.md</code> files under app-owned roots such as <code>backend</code>, <code>scripts</code>, <code>frontend</code>, <code>ui</code>, and <code>docs</code>.</p></div>
        <div class="card"><h3>Excluded</h3><p><code>.venv</code>, <code>node_modules</code>, <code>.git</code>, caches, export folders, calendar tokens, QA artifacts, and uploaded resume/document files. These are runtime/generated/user files, not application source.</p></div>
        <div class="card"><h3>Database Definition</h3><p>The database-like store count includes configured Azure PostgreSQL, local SQLite <code>.db</code> files, and JSON operational stores. JSON “fields” are distinct key names recursively found in those stores.</p></div>
        <div class="card"><h3>Keep Updated</h3><p>Run <code>python docs/generate_application_summary.py</code> from the repo root after major changes to refresh this page.</p></div>
      </div>
      <div class="callout"><strong>Privacy:</strong> The generator checks schema metadata and file sizes/counts only. It does not print database passwords, API keys, OAuth secrets, or private credential values.</div>
    </section>
  </main>
</body>
</html>"""


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(OUT)

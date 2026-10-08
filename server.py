#!/usr/bin/env python3
"""AIDB companion server.

Serves the UI from this folder and bridges MySQL / PostgreSQL / local-file
SQLite to the browser app. Stdlib only for SQLite; MySQL needs
`pip install pymysql`, PostgreSQL needs `pip install psycopg2-binary`.

Binds to 127.0.0.1 only. Port: DBX_PORT env or 8788.
"""
import json
import os
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("DBX_HOST", "127.0.0.1")
PORT = int(os.environ.get("DBX_PORT", "8788"))

CONNS = {}
CONNS_LOCK = threading.Lock()


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def enc_default(o):
    if isinstance(o, bytes):
        try:
            return o.decode("utf-8")
        except UnicodeDecodeError:
            return "<binary %d bytes>" % len(o)
    if hasattr(o, "isoformat"):
        return o.isoformat(sep=" ")
    return str(o)


def have(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def get_conn(cid):
    with CONNS_LOCK:
        c = CONNS.get(cid)
    if not c:
        raise ApiError("连接不存在或已断开", 404)
    return c


def quote_ident(dialect, name):
    if dialect == "mysql":
        return "`" + str(name).replace("`", "``") + "`"
    if dialect == "mssql":
        return "[" + str(name).replace("]", "]]") + "]"
    return '"' + str(name).replace('"', '""') + '"'


def adapt_sql(dialect, sql, params):
    # Client-side % inside WHERE (e.g. LIKE 'a%') must survive %s interpolation.
    if dialect != "sqlite" and params:
        sql = sql.replace("%", "%%").replace("?", "%s")
    return sql


def open_sqlite(path):
    path = os.path.normpath(path)
    if not os.path.exists(path):
        raise ApiError("文件不存在: %s" % path)
    try:
        conn = sqlite3.connect(path, timeout=5, check_same_thread=False,
                               isolation_level=None)
        conn.execute("SELECT 1")
    except sqlite3.Error as e:
        raise ApiError("无法打开 SQLite 文件: %s" % e)
    return {"dialect": "sqlite", "conn": conn, "lock": threading.Lock(), "db": None}


def open_mysql(p):
    try:
        import pymysql
    except ImportError:
        raise ApiError("服务器未安装 pymysql：pip install pymysql", 500)
    try:
        conn = pymysql.connect(
            host=p.get("host") or "127.0.0.1",
            port=int(p.get("port") or 3306),
            user=p.get("user") or "root",
            password=p.get("password") or "",
            database=p.get("database") or "",
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=6,
        )
    except Exception as e:
        raise ApiError("MySQL 连接失败: %s" % e)
    return {"dialect": "mysql", "conn": conn, "lock": threading.Lock(),
            "db": p.get("database") or ""}


def open_pg(p):
    try:
        import psycopg2
    except ImportError:
        raise ApiError("服务器未安装 psycopg2：pip install psycopg2-binary", 500)
    try:
        conn = psycopg2.connect(
            host=p.get("host") or "127.0.0.1",
            port=int(p.get("port") or 5432),
            user=p.get("user") or "postgres",
            password=p.get("password") or "",
            dbname=p.get("database") or "postgres",
            connect_timeout=6,
        )
        conn.autocommit = True
    except Exception as e:
        raise ApiError("PostgreSQL 连接失败: %s" % e)
    return {"dialect": "pg", "conn": conn, "lock": threading.Lock(),
            "db": p.get("database") or "postgres"}


def open_mssql(p):
    try:
        import pymssql
    except ImportError:
        raise ApiError("服务器未安装 pymssql：pip install pymssql", 500)
    try:
        conn = pymssql.connect(
            server=p.get("host") or "127.0.0.1",
            port=str(p.get("port") or 1433),
            user=p.get("user") or "sa",
            password=p.get("password") or "",
            database=p.get("database") or "",
            charset="utf8",
            login_timeout=6,
        )
        conn.autocommit(True)
    except Exception as e:
        raise ApiError("SQL Server 连接失败: %s" % e)
    return {"dialect": "mssql", "conn": conn, "lock": threading.Lock(),
            "db": p.get("database") or ""}


def handle_connect(payload):
    t = payload.get("type")
    if t == "sqlite":
        c = open_sqlite(payload.get("path", "").strip())
    elif t == "mysql":
        c = open_mysql(payload)
    elif t in ("postgres", "postgresql"):
        c = open_pg(payload)
    elif t in ("mssql", "sqlserver"):
        c = open_mssql(payload)
    else:
        raise ApiError("未知数据库类型: %s" % t)
    cid = uuid.uuid4().hex[:12]
    with CONNS_LOCK:
        CONNS[cid] = c
    return {"conn_id": cid, "dialect": c["dialect"]}


def list_tables(c):
    d, conn = c["dialect"], c["conn"]
    cur = conn.cursor()
    if d == "sqlite":
        rows = conn.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY (type='table') DESC, name").fetchall()
        return [{"name": r[0], "type": r[1]} for r in rows]
    if d == "mysql":
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name",
            (c["db"],))
        return [{"name": r[0], "type": "table"} for r in cur.fetchall()]
    if d == "mssql":
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_type='BASE TABLE' AND table_schema = SCHEMA_NAME() "
            "ORDER BY table_name")
        return [{"name": r[0], "type": "table"} for r in cur.fetchall()]
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
    return [{"name": r[0], "type": "table"} for r in cur.fetchall()]


def describe(c, table):
    d, conn = c["dialect"], c["conn"]
    if d == "sqlite":
        rows = conn.execute("PRAGMA table_info(%s)" % quote_ident(d, table)).fetchall()
        return [{"name": r[1], "type": r[2] or "", "notnull": bool(r[3]),
                 "pk": int(r[5] or 0)} for r in rows]
    cur = conn.cursor()
    if d == "mysql":
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_key "
            "FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
            (c["db"], table))
        return [{"name": r[0], "type": r[1], "notnull": r[2] == "NO",
                 "pk": 1 if r[3] == "PRI" else 0} for r in cur.fetchall()]
    if d == "mssql":
        cur.execute(
            "SELECT c.column_name, c.data_type, c.is_nullable, "
            "CASE WHEN pk.column_name IS NULL THEN 0 ELSE 1 END "
            "FROM information_schema.columns c "
            "LEFT JOIN (SELECT ku.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage ku "
            "ON tc.constraint_name = ku.constraint_name "
            "AND tc.table_schema = ku.table_schema "
            "WHERE tc.constraint_type = 'PRIMARY KEY' "
            "AND tc.table_schema = SCHEMA_NAME() AND tc.table_name = %s) pk "
            "ON pk.column_name = c.column_name "
            "WHERE c.table_schema = SCHEMA_NAME() AND c.table_name = %s "
            "ORDER BY c.ordinal_position", (table, table))
        return [{"name": r[0], "type": r[1], "notnull": r[2] == "NO",
                 "pk": int(r[3])} for r in cur.fetchall()]
    cur.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (table,))
    cols = [{"name": r[0], "type": r[1], "notnull": r[2] == "NO", "pk": 0}
            for r in cur.fetchall()]
    cur.execute(
        "SELECT kcu.column_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_name = kcu.constraint_name "
        "AND tc.table_schema = kcu.table_schema "
        "WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='public' "
        "AND tc.table_name=%s", (table,))
    pks = {r[0] for r in cur.fetchall()}
    for col in cols:
        if col["name"] in pks:
            col["pk"] = 1
    return cols


def fetch_rows(c, table, limit, offset, where, sort, direction):
    d = c["dialect"]
    qi = quote_ident(d, table)
    sql = "SELECT * FROM " + qi
    cnt = "SELECT COUNT(*) FROM " + qi
    base_params = []
    if where:
        sql += " WHERE " + where
        cnt += " WHERE " + where
    # Pagination placeholders stay '?' here; adapt_sql translates per dialect.
    if d == "mssql":
        if sort:
            direction = "DESC" if str(direction).upper() == "DESC" else "ASC"
            sql += " ORDER BY " + quote_ident(d, sort) + " " + direction
        else:
            sql += " ORDER BY (SELECT NULL)"
        sql += " OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
        params_all = base_params + [offset, limit]
    else:
        if sort:
            direction = "DESC" if str(direction).upper() == "DESC" else "ASC"
            sql += " ORDER BY " + quote_ident(d, sort) + " " + direction
        sql += " LIMIT ? OFFSET ?"
        params_all = base_params + [limit, offset]
    cur = c["conn"].cursor()
    with c["lock"]:
        cur.execute(adapt_sql(d, sql, params_all), params_all)
        columns = [x[0] for x in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchmany(min(int(limit), 5000))]
        cur.execute(adapt_sql(d, cnt, base_params), base_params)
        total = cur.fetchone()[0]
    return {"columns": columns, "rows": rows, "total": total}


def run_query(c, sql, params):
    d = c["dialect"]
    cur = c["conn"].cursor()
    with c["lock"]:
        if d != "sqlite" and params:
            sql = sql.replace("%", "%%").replace("?", "%s")
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        columns = [x[0] for x in cur.description] if cur.description else []
        rows, truncated = [], False
        if columns:
            rows = [list(r) for r in cur.fetchmany(2000)]
            truncated = len(rows) == 2000
        try:
            rowcount = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        except Exception:
            rowcount = 0
    return {"columns": columns, "rows": rows, "rowcount": rowcount,
            "truncated": truncated}


def handle_disconnect(payload):
    cid = payload.get("conn")
    with CONNS_LOCK:
        c = CONNS.pop(cid, None)
    if c:
        try:
            c["conn"].close()
        except Exception:
            pass
    return {"ok": True}


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".wasm": "application/wasm",
    ".db": "application/octet-stream",
    ".sqlite": "application/octet-stream",
    ".db3": "application/octet-stream",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "AIDB/1.0"

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False, default=enc_default).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _qs1(self, qs, key, default=None):
        v = qs.get(key)
        return v[0] if v else default

    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        try:
            if path == "/api/ping":
                self._send_json(200, {
                    "ok": True, "name": "aidb",
                    "mysql": have("pymysql"), "pg": have("psycopg2"),
                    "mssql": have("pymssql"),
                })
            elif path == "/api/tables":
                c = get_conn(self._qs1(qs, "conn", ""))
                self._send_json(200, {"tables": list_tables(c)})
            elif path == "/api/describe":
                c = get_conn(self._qs1(qs, "conn", ""))
                table = self._qs1(qs, "table", "")
                if not table:
                    raise ApiError("缺少 table 参数")
                self._send_json(200, {"columns": describe(c, table)})
            elif path == "/api/rows":
                c = get_conn(self._qs1(qs, "conn", ""))
                table = self._qs1(qs, "table", "")
                if not table:
                    raise ApiError("缺少 table 参数")
                limit = min(int(self._qs1(qs, "limit", "50")), 5000)
                offset = max(int(self._qs1(qs, "offset", "0")), 0)
                res = fetch_rows(c, table, limit, offset,
                                 self._qs1(qs, "where"), self._qs1(qs, "sort"),
                                 self._qs1(qs, "dir", "ASC"))
                self._send_json(200, res)
            else:
                self.serve_static(path)
        except ApiError as e:
            self._send_json(e.code, {"error": str(e)})
        except Exception as e:
            self._send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            if parsed.path == "/api/connect":
                self._send_json(200, handle_connect(payload))
            elif parsed.path == "/api/query":
                c = get_conn(payload.get("conn", ""))
                sql = payload.get("sql", "")
                if not sql.strip():
                    raise ApiError("SQL 为空")
                self._send_json(200, run_query(c, sql, payload.get("params")))
            elif parsed.path == "/api/disconnect":
                self._send_json(200, handle_disconnect(payload))
            else:
                raise ApiError("未知接口: %s" % parsed.path, 404)
        except ApiError as e:
            self._send_json(e.code, {"error": str(e)})
        except Exception as e:
            self._send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def serve_static(self, path):
        if path in ("/", "/index.html"):
            fp = os.path.join(ROOT, "index.html")
        else:
            rel = unquote(path.lstrip("/"))
            fp = os.path.normpath(os.path.join(ROOT, rel))
            if not fp.startswith(ROOT):
                self.send_error(403)
                return
        if not os.path.isfile(fp):
            self.send_error(404)
            return
        ext = os.path.splitext(fp)[1].lower()
        with open(fp, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("AIDB server")
    print("  serving : http://%s:%d" % (HOST, PORT))
    print("  mysql   :", "driver ok" if have("pymysql") else "not installed (pip install pymysql)")
    print("  postgres:", "driver ok" if have("psycopg2") else "not installed (pip install psycopg2-binary)")
    print("  mssql   :", "driver ok" if have("pymssql") else "not installed (pip install pymssql)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

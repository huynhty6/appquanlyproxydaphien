"""
HT Proxy — Public API Server
Chạy: python server.py

API xoay IP:
  GET /api/{interface}/{host}/{token}
  GET /api/all/{host}/{token}
  GET /api/status/{host}/{token}
"""

import os
import time
import secrets
import logging
import asyncio
from contextlib import asynccontextmanager

import aiosqlite
import routeros_api
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("api_server")

DB_PATH = os.path.join(os.path.dirname(__file__), "api_data.db")
DEFAULT_MIKROTIK_PORT = 3544
MAX_RETRY = 5


# ──────────────────────── Database ────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS routers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                host TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL,
                password TEXT NOT NULL DEFAULT '',
                port INTEGER NOT NULL DEFAULT 3544,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id INTEGER,
                source TEXT NOT NULL DEFAULT 'api',
                host TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                interface TEXT NOT NULL DEFAULT '',
                old_ip TEXT NOT NULL DEFAULT '',
                new_ip TEXT NOT NULL DEFAULT '',
                retries INTEGER NOT NULL DEFAULT 0,
                success INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                ip_address TEXT NOT NULL DEFAULT '',
                elapsed_ms INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at);
            CREATE INDEX IF NOT EXISTS idx_tokens_token ON tokens(token);
            CREATE INDEX IF NOT EXISTS idx_routers_host ON routers(host);
        """)
        try:
            await db.execute("ALTER TABLE logs ADD COLUMN source TEXT NOT NULL DEFAULT 'api'")
        except Exception:
            pass
        await db.commit()


async def get_db():
    return await aiosqlite.connect(DB_PATH)


# ──────────────────── MikroTik ────────────────────

def mk_connect(host, username, password, port=None):
    pool = routeros_api.RouterOsApiPool(
        host, username=username, password=password,
        port=port or DEFAULT_MIKROTIK_PORT, plaintext_login=True, use_ssl=False,
    )
    return pool.get_api()


def mk_get_ip_map(api):
    result = {}
    for addr in api.get_resource("/ip/address").get():
        iface = addr.get("interface", "")
        ip = addr.get("address", "").split("/")[0]
        if iface:
            result[iface] = ip
    return result


def mk_get_pppoe_status(api):
    result = []
    ip_map = mk_get_ip_map(api)
    for iface in api.get_resource("/interface/pppoe-client").get():
        name = iface.get("name", "")
        running = iface.get("running", "false") == "true"
        disabled = iface.get("disabled", "false") == "true"
        result.append({
            "name": name, "running": running, "disabled": disabled,
            "ip": ip_map.get(name, ""),
        })
    return result


def mk_rotate_single(api, interface_name, max_retry=MAX_RETRY):
    if interface_name == "pppoe-out1":
        return {
            "success": False, "interface": interface_name,
            "message": "pppoe-out1 là cổng điều khiển, không xoay được",
            "old_ip": "", "new_ip": "", "retries": 0,
        }

    ip_map = mk_get_ip_map(api)
    old_ip = ip_map.get(interface_name, "")
    other_ips = {v for k, v in ip_map.items() if k != interface_name and v}

    pppoe_res = api.get_resource("/interface/pppoe-client")
    items = pppoe_res.get(name=interface_name)
    if not items:
        return {
            "success": False, "interface": interface_name,
            "message": f"Interface {interface_name} không tồn tại",
            "old_ip": old_ip, "new_ip": "", "retries": 0,
        }
    item_id = items[0]["id"]

    for attempt in range(1, max_retry + 1):
        pppoe_res.set(id=item_id, disabled="true")
        time.sleep(3)
        pppoe_res.set(id=item_id, disabled="false")

        new_ip = ""
        for _ in range(20):
            time.sleep(2)
            new_map = mk_get_ip_map(api)
            new_ip = new_map.get(interface_name, "")
            if new_ip:
                break

        if not new_ip:
            continue

        if new_ip != old_ip and new_ip not in other_ips:
            return {
                "success": True, "interface": interface_name,
                "old_ip": old_ip, "new_ip": new_ip,
                "retries": attempt - 1,
                "message": f"Đã xoay IP thành công cho {interface_name}" +
                           (f" (sau {attempt} lần thử)" if attempt > 1 else ""),
            }
        other_ips.add(new_ip)

    final_ip = mk_get_ip_map(api).get(interface_name, "")
    return {
        "success": False, "interface": interface_name,
        "old_ip": old_ip, "new_ip": final_ip, "retries": max_retry,
        "message": f"Không thể xoay IP sau {max_retry} lần thử" +
                   (" (IP trùng)" if final_ip and final_ip in other_ips else " (timeout)"),
    }


def mk_rotate_all(api, skip=None, max_retry=MAX_RETRY):
    if skip is None:
        skip = ["pppoe-out1"]

    old_map = mk_get_ip_map(api)
    pppoe_res = api.get_resource("/interface/pppoe-client")
    all_ifaces = pppoe_res.get()
    targets = [i for i in all_ifaces if i.get("name") not in skip and i.get("disabled", "false") != "true"]

    for i in targets:
        try:
            pppoe_res.set(id=i["id"], disabled="true")
        except Exception:
            pass
    time.sleep(3)
    for i in targets:
        try:
            pppoe_res.set(id=i["id"], disabled="false")
        except Exception:
            pass

    for _ in range(25):
        time.sleep(2)
        new_map = mk_get_ip_map(api)
        if all(new_map.get(i.get("name", "")) for i in targets):
            break

    final_map = mk_get_ip_map(api)
    results = []
    for i in targets:
        name = i.get("name", "")
        results.append({
            "interface": name, "old_ip": old_map.get(name, ""),
            "new_ip": final_map.get(name, ""),
            "success": bool(final_map.get(name, "")) and final_map.get(name, "") != old_map.get(name, ""),
        })
    return results


# ──────────────────── Lifespan ────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("API Server ready — DB: %s", DB_PATH)
    yield


# ──────────────────── FastAPI App ────────────────────

app = FastAPI(title="HT Proxy Public API", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


async def log_action(db, token_id, host, action, interface="", old_ip="", new_ip="",
                     retries=0, success=0, message="", ip_address="", elapsed_ms=0, source="api"):
    await db.execute(
        "INSERT INTO logs (token_id, source, host, action, interface, old_ip, new_ip, retries, success, message, ip_address, elapsed_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (token_id, source, host, action, interface, old_ip, new_ip, retries, success, message, ip_address, elapsed_ms)
    )
    await db.commit()


async def verify_token(token: str, db):
    cursor = await db.execute("SELECT id, is_active FROM tokens WHERE token = ?", (token,))
    row = await cursor.fetchone()
    if not row:
        return None, "Token không hợp lệ"
    if not row[1]:
        return None, "Token đã bị vô hiệu hóa"
    return row[0], None


async def get_router_creds(host: str, db):
    """Tìm router credentials từ bảng routers (dùng chung cho mọi token)."""
    cursor = await db.execute("SELECT host, username, password, port FROM routers WHERE host = ?", (host,))
    row = await cursor.fetchone()
    if not row:
        return None, f"Router '{host}' chưa được đăng ký. Vào tab API trên app để đồng bộ router."
    return {"host": row[0], "username": row[1], "password": row[2], "port": row[3]}, None


# ──────────────── Public API ────────────────

@app.get("/")
async def root():
    return {
        "service": "HT Proxy API",
        "version": "2.0.0",
        "usage": "/api/{interface}/{host}/{token}",
        "docs": "/docs",
    }


@app.get("/api/{interface}/{host}/{token}")
async def api_rotate(interface: str, host: str, token: str, request: Request):
    start = time.time()
    client_ip = request.client.host if request.client else ""
    db = await get_db()
    try:
        token_id, err = await verify_token(token, db)
        if err:
            return JSONResponse({"success": False, "message": err}, status_code=403)

        router, err = await get_router_creds(host, db)
        if err:
            return JSONResponse({"success": False, "message": err}, status_code=404)

        try:
            api = await asyncio.to_thread(mk_connect, router["host"], router["username"], router["password"], router.get("port"))
        except Exception as e:
            msg = f"Không kết nối được router: {str(e)[:100]}"
            await log_action(db, token_id, host, "rotate", interface, success=0, message=msg, ip_address=client_ip)
            return JSONResponse({"success": False, "host": host, "interface": interface, "message": msg}, status_code=502)

        try:
            if interface.lower() == "all":
                results = await asyncio.to_thread(mk_rotate_all, api)
                elapsed = int((time.time() - start) * 1000)
                ok_count = sum(1 for r in results if r["success"])
                msg = f"Đã xoay {ok_count}/{len(results)} proxy"
                await log_action(db, token_id, host, "rotate-all", "all",
                                 retries=0, success=1 if ok_count > 0 else 0,
                                 message=msg, ip_address=client_ip, elapsed_ms=elapsed)
                return {
                    "success": ok_count > 0, "host": host, "message": msg,
                    "total": len(results), "rotated": ok_count,
                    "results": results, "elapsed_ms": elapsed,
                }
            else:
                result = await asyncio.to_thread(mk_rotate_single, api, interface)
                elapsed = int((time.time() - start) * 1000)
                await log_action(db, token_id, host, "rotate", interface,
                                 result.get("old_ip", ""), result.get("new_ip", ""),
                                 result.get("retries", 0), 1 if result["success"] else 0,
                                 result["message"], client_ip, elapsed)
                return {
                    "success": result["success"], "host": host,
                    "interface": result["interface"],
                    "old_ip": result.get("old_ip", ""),
                    "new_ip": result.get("new_ip", ""),
                    "retries": result.get("retries", 0),
                    "message": result["message"], "elapsed_ms": elapsed,
                }
        finally:
            try:
                api.get_binary_api_socket().close()
            except Exception:
                pass
    finally:
        await db.close()


@app.get("/api/status/{host}/{token}")
async def api_status(host: str, token: str, request: Request):
    client_ip = request.client.host if request.client else ""
    db = await get_db()
    try:
        token_id, err = await verify_token(token, db)
        if err:
            return JSONResponse({"success": False, "message": err}, status_code=403)

        router, err = await get_router_creds(host, db)
        if err:
            return JSONResponse({"success": False, "message": err}, status_code=404)

        try:
            api = await asyncio.to_thread(mk_connect, router["host"], router["username"], router["password"], router.get("port"))
        except Exception as e:
            return JSONResponse({"success": False, "host": host,
                                 "message": f"Không kết nối được: {str(e)[:100]}"}, status_code=502)
        try:
            proxies = await asyncio.to_thread(mk_get_pppoe_status, api)
            await log_action(db, token_id, host, "status", ip_address=client_ip)
            online = sum(1 for p in proxies if p["running"] and p["ip"])
            return {"success": True, "host": host, "total": len(proxies), "online": online, "proxies": proxies}
        finally:
            try:
                api.get_binary_api_socket().close()
            except Exception:
                pass
    finally:
        await db.close()



# ──────────────── Router & Token Management ────────────────

@app.post("/sync-routers")
async def sync_routers(request: Request):
    """Đồng bộ danh sách router từ app desktop. Gọi khi user mở tab API."""
    body = await request.json()
    routers = body.get("routers", [])
    if not routers:
        return JSONResponse({"ok": False, "error": "Không có router"}, status_code=400)

    db = await get_db()
    try:
        synced = 0
        for r in routers:
            host = r.get("host", "").strip()
            username = r.get("username", "").strip() or r.get("user", "").strip()
            port = int(r.get("port", 0)) or DEFAULT_MIKROTIK_PORT
            if not host:
                continue
            cursor = await db.execute("SELECT id FROM routers WHERE host = ?", (host,))
            existing = await cursor.fetchone()
            if existing:
                await db.execute(
                    "UPDATE routers SET name = ?, username = ?, password = ?, port = ? WHERE id = ?",
                    (r.get("name", ""), username, r.get("password", ""), port, existing[0])
                )
            else:
                await db.execute(
                    "INSERT INTO routers (name, host, username, password, port) VALUES (?, ?, ?, ?, ?)",
                    (r.get("name", ""), host, username, r.get("password", ""), port)
                )
            synced += 1
        await db.commit()
        return {"ok": True, "synced": synced}
    finally:
        await db.close()


@app.get("/list-routers")
async def list_routers():
    db = await get_db()
    try:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, name, host FROM routers ORDER BY id")
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


@app.post("/create-token")
async def create_token(request: Request):
    body = await request.json()
    name = body.get("name", "")
    token_str = "ht_" + secrets.token_hex(20)
    db = await get_db()
    try:
        await db.execute("INSERT INTO tokens (token, name) VALUES (?, ?)", (token_str, name))
        await db.commit()

        cursor = await db.execute("SELECT COUNT(*) FROM routers")
        router_count = (await cursor.fetchone())[0]
        return {"ok": True, "token": token_str, "routers_count": router_count}
    finally:
        await db.close()


@app.get("/list-tokens")
async def list_tokens():
    db = await get_db()
    try:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tokens ORDER BY id DESC")
        tokens = [dict(r) for r in await cursor.fetchall()]

        cursor = await db.execute("SELECT id, name, host FROM routers ORDER BY id")
        all_routers = [dict(r) for r in await cursor.fetchall()]

        for t in tokens:
            t["routers"] = all_routers

        return tokens
    finally:
        await db.close()


@app.delete("/delete-token/{token_id}")
async def delete_token(token_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@app.put("/toggle-token/{token_id}")
async def toggle_token(token_id: int, request: Request):
    body = await request.json()
    db = await get_db()
    try:
        await db.execute("UPDATE tokens SET is_active = ? WHERE id = ?", (body.get("is_active", 1), token_id))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@app.get("/logs")
async def get_logs(limit: int = 200, offset: int = 0):
    db = await get_db()
    try:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT l.*, t.name as token_name FROM logs l "
            "LEFT JOIN tokens t ON l.token_id = t.id "
            "ORDER BY l.id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = [dict(r) for r in await cursor.fetchall()]
        cnt = await db.execute("SELECT COUNT(*) FROM logs")
        total = (await cnt.fetchone())[0]
        return {"total": total, "logs": rows}
    finally:
        await db.close()


@app.delete("/logs")
async def clear_logs():
    db = await get_db()
    try:
        await db.execute("DELETE FROM logs")
        await db.commit()
        return {"ok": True, "message": "All logs cleared"}
    finally:
        await db.close()


@app.post("/log-rotate")
async def log_rotate_from_app(request: Request):
    """App desktop gửi log khi xoay IP qua app (không phải qua API link)."""
    body = await request.json()
    db = await get_db()
    try:
        await log_action(
            db, token_id=None,
            host=body.get("host", ""),
            action=body.get("action", "rotate"),
            interface=body.get("interface", ""),
            old_ip=body.get("old_ip", ""),
            new_ip=body.get("new_ip", ""),
            retries=body.get("retries", 0),
            success=1 if body.get("success") else 0,
            message=body.get("message", ""),
            elapsed_ms=body.get("elapsed_ms", 0),
            source="app",
        )
        return {"ok": True}
    finally:
        await db.close()


# ──────────────────── Run ────────────────────

if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  HT Proxy — Public API Server v2")
    print("  http://0.0.0.0:3000")
    print("  Docs: http://0.0.0.0:3000/docs")
    print()
    print("  API: /api/{interface}/{host}/{token}")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=3000, log_level="info")

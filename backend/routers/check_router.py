import asyncio
import time
import socket
from datetime import datetime, timezone
from fastapi import APIRouter, Depends

from auth import get_current_user
from firebase_app import get_db
from schemas import ProxyCheckRequest

router = APIRouter(prefix="/api/proxy-check", tags=["Proxy Check"])


def _check_one(ip: str, port: int, timeout: int) -> dict:
    start = time.time()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.close()
        latency = round((time.time() - start) * 1000, 1)
        return {"ip": ip, "port": port, "status": "alive", "latency_ms": latency, "country": "", "last_checked": datetime.now(timezone.utc).isoformat()}
    except Exception:
        return {"ip": ip, "port": port, "status": "dead", "latency_ms": 0, "country": "", "last_checked": datetime.now(timezone.utc).isoformat()}


@router.post("/start")
async def start_check(
    req: ProxyCheckRequest,
    _user: dict = Depends(get_current_user),
):
    db = get_db()
    docs = db.collection("proxies").get()
    proxies = []
    for doc in docs:
        data = doc.to_dict()
        data["_id"] = doc.id
        if req.proxy_ids and doc.id not in req.proxy_ids:
            continue
        proxies.append(data)

    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, _check_one, p["ip"], p.get("http_port", 10001), req.timeout_seconds)
        for p in proxies
    ]
    results = await asyncio.gather(*tasks)

    alive = 0
    for r, p in zip(results, proxies):
        new_status = "online" if r["status"] == "alive" else "offline"
        if r["status"] == "alive":
            alive += 1
        db.collection("proxies").document(p["_id"]).update({"status": new_status})

    return {
        "total": len(results),
        "alive": alive,
        "dead": len(results) - alive,
        "results": results,
    }

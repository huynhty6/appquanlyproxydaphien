from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from firebase_app import get_db
from mikrotik_client import MikroTikClient, get_mikrotik_for_user
from schemas import (
    ProxyResponse, ProxyCreate, ProxyAssignRequest,
    DashboardStats, RotateRequest, RotationResponse,
    MikroTikConnectRequest, MikroTikStatusResponse,
    ChangeProxyCredsRequest, ChangeAllProxyCredsRequest,
)

router = APIRouter(prefix="/api", tags=["Proxy"])


def _get_mk(user: dict) -> MikroTikClient:
    try:
        return get_mikrotik_for_user(user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- MikroTik Test (khong can auth - de test ket noi) ----------

@router.post("/mikrotik/test")
async def mikrotik_test(req: MikroTikConnectRequest):
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    result = client.test_connection()
    return result


# ---------- MikroTik Connect (user tu nhap host/user/pass, port 2601 an) ----------

@router.post("/mikrotik/connect", response_model=MikroTikStatusResponse)
async def mikrotik_connect(
    req: MikroTikConnectRequest,
    user: dict = Depends(get_current_user),
):
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    result = client.test_connection()
    if not result.get("connected"):
        raise HTTPException(status_code=400, detail=result.get("error", "Ket noi that bai"))

    db = get_db()
    db.collection("users").document(user["id"]).update({
        "mikrotik": {
            "host": req.host,
            "username": req.username,
            "password": req.password,
        }
    })

    return MikroTikStatusResponse(**result)


@router.get("/mikrotik/status", response_model=MikroTikStatusResponse)
async def mikrotik_status(user: dict = Depends(get_current_user)):
    mk = _get_mk(user)
    result = mk.test_connection()
    return MikroTikStatusResponse(**result)


@router.post("/mikrotik/disconnect")
async def mikrotik_disconnect(user: dict = Depends(get_current_user)):
    db = get_db()
    db.collection("users").document(user["id"]).update({"mikrotik": {}})
    return {"status": "ok", "message": "Da ngat ket noi MikroTik"}


# ---------- PPPoE Clients (doc IP tu MikroTik) ----------

@router.post("/mikrotik/pppoe-clients")
async def mikrotik_pppoe_clients(req: MikroTikConnectRequest):
    """Doc bang PPPoE client + IP + proxy credentials tu MikroTik.
    Dung container -> envlist de map chinh xac credentials."""
    import re as _re
    import logging
    _log = logging.getLogger("proxy_router")

    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        interfaces = client.list_pppoe_interfaces()
        ip_addresses = client.list_ip_addresses()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        creds_map = client.get_all_proxy_credentials()
    except Exception as exc:
        _log.warning("Cannot read container credentials: %s", exc)
        creds_map = {}

    _log.info("=== creds_map keys=%s ===", list(creds_map.keys()))
    for num, info in creds_map.items():
        _log.info("  [%d] envlist=%s login=%r pass=%r cid=%s",
                   num, info.get("envlist"), info.get("proxy_login"),
                   info.get("proxy_password"), info.get("container_id"))

    ip_map = {}
    for ip in ip_addresses:
        iface = ip.get("interface", "")
        addr = ip.get("address", "").split("/")[0]
        if iface:
            ip_map[iface] = addr

    result = []
    for idx, iface in enumerate(interfaces, 1):
        name = iface.get("name", "")
        running = iface.get("running", "false") == "true"
        disabled = iface.get("disabled", "false") == "true"

        if disabled:
            pppoe_status = "disabled"
        elif running:
            pppoe_status = "online"
        else:
            pppoe_status = "offline"

        num_match = _re.search(r"(\d+)$", name)
        num = int(num_match.group(1)) if num_match else idx
        http_port = 10000 + num
        socks_port = 20000 + num

        cred = creds_map.get(num, {})
        proxy_login = cred.get("proxy_login", "")
        proxy_password = cred.get("proxy_password", "")
        container_id = cred.get("container_id", "")
        envlist = cred.get("envlist", "")

        result.append({
            "stt": idx,
            "name": name,
            "ip": ip_map.get(name, "") if pppoe_status == "online" else "",
            "status": pppoe_status,
            "user": iface.get("user", ""),
            "service_name": iface.get("service-name", ""),
            "interface": iface.get("interface", ""),
            "uptime": iface.get("uptime", ""),
            "http_port": http_port,
            "socks_port": socks_port,
            "proxy_login": proxy_login,
            "proxy_password": proxy_password,
            "container_id": container_id,
            "envlist": envlist,
        })
    return {"total": len(result), "host": req.host, "clients": result}


@router.post("/mikrotik/restart-container")
async def mikrotik_restart_container(req: MikroTikConnectRequest, container_id: str = ""):
    """Restart 1 container (stop -> wait -> start). Dung container .id."""
    if not container_id:
        raise HTTPException(status_code=400, detail="Thieu container_id")
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        result = client.restart_container(container_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/mikrotik/restart-all-containers")
async def mikrotik_restart_all_containers(req: MikroTikConnectRequest):
    """Restart tat ca container proxy (stop all -> wait 5s -> start all -> poll running)."""
    import time as _time
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        containers = client.list_containers()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    ids = [ct.get("id", ct.get(".id", "")) for ct in containers if ct.get("id", ct.get(".id", ""))]
    if not ids:
        return {"status": "ok", "total": 0, "results": []}

    for cid in ids:
        try:
            client.stop_container(cid)
        except Exception:
            pass

    _time.sleep(5)

    for cid in ids:
        try:
            client.start_container(cid)
        except Exception:
            pass

    for attempt in range(6):
        _time.sleep(5)
        try:
            cts = client.list_containers()
        except Exception:
            continue
        statuses = {ct.get("id", ct.get(".id", "")): ct.get("status", "") for ct in cts}
        all_running = all(statuses.get(cid) == "running" for cid in ids)
        if all_running:
            break

    try:
        final_cts = client.list_containers()
    except Exception:
        final_cts = []
    final_map = {ct.get("id", ct.get(".id", "")): ct.get("status", "") for ct in final_cts}

    not_running = [cid for cid in ids if final_map.get(cid) != "running"]
    for cid in not_running:
        try:
            client.start_container(cid)
        except Exception:
            pass
    if not_running:
        _time.sleep(5)
        try:
            final_cts = client.list_containers()
            final_map = {ct.get("id", ct.get(".id", "")): ct.get("status", "") for ct in final_cts}
        except Exception:
            pass

    results = [{"id": cid, "status": final_map.get(cid, "unknown")} for cid in ids]
    return {
        "status": "ok",
        "total": len(ids),
        "all_running": all(r["status"] == "running" for r in results),
        "results": results,
    }


@router.post("/mikrotik/containers")
async def mikrotik_containers_list(req: MikroTikConnectRequest):
    """Xem danh sach container (interface + envlist + status)."""
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        containers = client.list_containers()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    items = []
    for ct in containers:
        items.append({
            "id": ct.get("id", ct.get(".id", "")),
            "interface": ct.get("interface", ""),
            "envlist": ct.get("envlist", ""),
            "status": ct.get("status", ""),
        })
    return {"total": len(items), "containers": items}


# ---------- Doi mat khau Proxy ----------

def _gen_random_creds() -> tuple[str, str]:
    import random, string
    suffix = "".join(random.choices(string.ascii_letters, k=4))
    login = "htproxy" + suffix
    pwd_chars = "".join(random.choices(string.ascii_letters, k=13))
    return login, pwd_chars


def _resolve_creds(mode: str, login: str, pwd: str) -> tuple[str, str]:
    if mode == "clear":
        return "", ""
    if mode == "random":
        return _gen_random_creds()
    return login, pwd


@router.post("/mikrotik/change-proxy-creds")
async def change_proxy_creds(req: ChangeProxyCredsRequest):
    """Doi mat khau 1 proxy: manual/random/clear -> update env -> restart container."""
    import time as _time

    if not req.envlist:
        raise HTTPException(status_code=400, detail="Thieu envlist")

    new_login, new_pwd = _resolve_creds(req.mode, req.proxy_login, req.proxy_password)
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)

    try:
        env_result = client.change_proxy_credentials(req.envlist, new_login, new_pwd)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Loi cap nhat env: {e}")

    container_status = ""
    if req.container_id:
        try:
            restart_result = client.restart_container(req.container_id)
            container_status = restart_result.get("container_status", "")
        except Exception as e:
            container_status = f"restart_error: {e}"

    return {
        "status": "ok",
        "envlist": req.envlist,
        "new_login": new_login,
        "new_password": new_pwd,
        "env_updated": env_result.get("updated", []),
        "container_id": req.container_id,
        "container_status": container_status,
    }


@router.post("/mikrotik/change-all-proxy-creds")
async def change_all_proxy_creds(req: ChangeAllProxyCredsRequest):
    """Doi mat khau tat ca proxy: update env tung cai -> restart all containers."""
    import time as _time

    client = MikroTikClient(host=req.host, username=req.username, password=req.password)

    try:
        creds_map = client.get_all_proxy_credentials()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    results = []
    for num, info in sorted(creds_map.items()):
        envlist = info.get("envlist", "")
        cid = info.get("container_id", "")
        if not envlist:
            continue

        if req.mode == "random":
            new_login, new_pwd = _gen_random_creds()
        else:
            new_login, new_pwd = _resolve_creds(req.mode, req.proxy_login, req.proxy_password)

        try:
            client.change_proxy_credentials(envlist, new_login, new_pwd)
            results.append({
                "num": num, "envlist": envlist, "container_id": cid,
                "new_login": new_login, "new_password": new_pwd, "status": "ok",
            })
        except Exception as e:
            results.append({"num": num, "envlist": envlist, "status": "error", "error": str(e)})

    container_ids = [r["container_id"] for r in results if r.get("status") == "ok" and r.get("container_id")]

    for cid in container_ids:
        try:
            client.stop_container(cid)
        except Exception:
            pass

    _time.sleep(5)

    for cid in container_ids:
        try:
            client.start_container(cid)
        except Exception:
            pass

    for attempt in range(6):
        _time.sleep(5)
        try:
            cts = client.list_containers()
        except Exception:
            continue
        statuses = {ct.get("id", ct.get(".id", "")): ct.get("status", "") for ct in cts}
        if all(statuses.get(cid) == "running" for cid in container_ids):
            break

    try:
        final_cts = client.list_containers()
        final_map = {ct.get("id", ct.get(".id", "")): ct.get("status", "") for ct in final_cts}
    except Exception:
        final_map = {}

    not_running = [cid for cid in container_ids if final_map.get(cid) != "running"]
    for cid in not_running:
        try:
            client.start_container(cid)
        except Exception:
            pass
    if not_running:
        _time.sleep(5)
        try:
            final_cts = client.list_containers()
            final_map = {ct.get("id", ct.get(".id", "")): ct.get("status", "") for ct in final_cts}
        except Exception:
            pass

    for r in results:
        cid = r.get("container_id", "")
        if cid:
            r["container_status"] = final_map.get(cid, "unknown")

    return {
        "status": "ok",
        "total": len(results),
        "all_running": all(final_map.get(r.get("container_id", "")) == "running" for r in results if r.get("container_id")),
        "results": results,
    }


# ---------- DHCP Leases ----------

@router.post("/mikrotik/dhcp-leases")
async def mikrotik_dhcp_leases(req: MikroTikConnectRequest):
    """Lay danh sach thiet bi tu DHCP lease."""
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        leases = client.list_dhcp_leases()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    mangle_map = {}
    try:
        mangles = client.list_mangle_rules()
        for m in mangles:
            if (m.get("chain") == "prerouting"
                    and m.get("action") == "mark-routing"
                    and m.get("src-address")):
                mangle_map[m["src-address"]] = {
                    "routing_mark": m.get("new-routing-mark", ""),
                    "mangle_id": m.get("id", m.get(".id", "")),
                    "comment": m.get("comment", ""),
                    "disabled": m.get("disabled", "false") == "true",
                }
    except Exception:
        pass

    ip_map = {}
    try:
        client2 = MikroTikClient(host=req.host, username=req.username, password=req.password)
        for ip in client2.list_ip_addresses():
            iface = ip.get("interface", "")
            addr = ip.get("address", "").split("/")[0]
            if iface:
                ip_map[iface] = addr
    except Exception:
        pass

    result = []
    for lease in leases:
        addr = lease.get("address", "")
        hostname = lease.get("host-name", "")
        mac = lease.get("mac-address", "")
        status = lease.get("status", "")
        comment = lease.get("comment", "")
        mangle = mangle_map.get(addr, {})
        routing_mark = mangle.get("routing_mark", "")

        import re
        pppoe_name = ""
        public_ip = ""
        if routing_mark:
            m = re.match(r"out-(pppoe-out\d+)", routing_mark)
            if m:
                pppoe_name = m.group(1)
                public_ip = ip_map.get(pppoe_name, "")

        result.append({
            "address": addr,
            "mac": mac,
            "hostname": hostname,
            "comment": comment,
            "status": status,
            "routing_mark": routing_mark,
            "pppoe_name": pppoe_name,
            "public_ip": public_ip,
            "mangle_id": mangle.get("mangle_id", ""),
            "mangle_comment": mangle.get("comment", ""),
            "assigned": bool(routing_mark),
        })
    return {"total": len(result), "devices": result}


@router.post("/mikrotik/assign-proxy")
async def assign_proxy_mangle(req: MikroTikConnectRequest,
                               src_address: str = "",
                               routing_mark: str = "",
                               comment: str = ""):
    """Gan proxy cho 1 thiet bi bang mangle rule."""
    if not src_address or not routing_mark:
        raise HTTPException(status_code=400, detail="Thieu src_address hoac routing_mark")
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        result = client.assign_proxy_mangle(src_address, routing_mark, comment)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/mikrotik/unassign-proxy")
async def unassign_proxy_mangle(req: MikroTikConnectRequest, src_address: str = ""):
    """Xoa gan proxy (xoa mangle rule) cho 1 thiet bi."""
    if not src_address:
        raise HTTPException(status_code=400, detail="Thieu src_address")
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        result = client.unassign_proxy_mangle(src_address)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/mikrotik/unassign-all")
async def unassign_all_proxy(req: MikroTikConnectRequest):
    """Xoa tat ca mangle rule proxy assignment (chi rule do HT Proxy tao, co comment 'htproxy')."""
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        mangles = client.list_mangle_rules()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    removed = 0
    for m in mangles:
        if (m.get("chain") == "prerouting"
                and m.get("action") == "mark-routing"
                and m.get("src-address")
                and client.HTPROXY_COMMENT in (m.get("comment") or "")):
            eid = m.get("id", m.get(".id", ""))
            try:
                client.remove_mangle_rule(eid)
                removed += 1
            except Exception:
                pass
    return {"status": "ok", "removed": removed}


@router.post("/mikrotik/assign-batch")
async def assign_batch(req: MikroTikConnectRequest,
                       mode: str = "sequential",
                       devices: str = "",
                       exclude: str = "",
                       proxy_start: int = 1,
                       proxy_end: int = 0):
    """Gan hang loat: sequential (theo thu tu), random (ngau nhien), round-robin (quay vong).
    devices: comma-separated src addresses. exclude: comma-separated to skip.
    proxy_start/proxy_end: pham vi pppoe-outN (1-based).
    """
    import random as _random
    import re as _re

    if not devices:
        raise HTTPException(status_code=400, detail="Thieu danh sach devices")

    device_list = [d.strip() for d in devices.split(",") if d.strip()]
    exclude_list = {e.strip() for e in exclude.split(",") if e.strip()}
    device_list = [d for d in device_list if d not in exclude_list]

    if not device_list:
        return {"status": "ok", "assigned": 0, "results": []}

    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    interfaces = client.list_pppoe_interfaces()
    pppoe_names = sorted(
        [i.get("name", "") for i in interfaces if i.get("name", "")],
        key=lambda n: int(_re.search(r"(\d+)$", n).group(1)) if _re.search(r"(\d+)$", n) else 0
    )

    if proxy_end <= 0:
        proxy_end = len(pppoe_names)
    available = []
    for name in pppoe_names:
        m = _re.search(r"(\d+)$", name)
        if m:
            num = int(m.group(1))
            if proxy_start <= num <= proxy_end:
                available.append(name)

    if not available:
        raise HTTPException(status_code=400, detail="Khong co proxy trong pham vi chi dinh")

    if mode == "random":
        _random.shuffle(available)

    ip_map = client.get_ip_map()
    results = []
    for i, addr in enumerate(device_list):
        proxy_idx = i % len(available)
        pppoe = available[proxy_idx]
        routing_mark = f"out-{pppoe}"
        public_ip = ip_map.get(pppoe, "")
        try:
            client.assign_proxy_mangle(addr, routing_mark)
            results.append({
                "address": addr,
                "pppoe": pppoe,
                "routing_mark": routing_mark,
                "public_ip": public_ip,
                "status": "ok",
            })
        except Exception as e:
            results.append({"address": addr, "pppoe": pppoe, "status": "error", "error": str(e)})

    return {
        "status": "ok",
        "mode": mode,
        "assigned": sum(1 for r in results if r["status"] == "ok"),
        "total": len(results),
        "results": results,
    }


@router.post("/mikrotik/proxy-list-for-assign")
async def proxy_list_for_assign(req: MikroTikConnectRequest):
    """Lay danh sach proxy (pppoe-outN) + IP de chon khi gan."""
    import re as _re
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    interfaces = client.list_pppoe_interfaces()
    ip_map = client.get_ip_map()
    result = []
    for iface in interfaces:
        name = iface.get("name", "")
        m = _re.search(r"(\d+)$", name)
        num = int(m.group(1)) if m else 0
        result.append({
            "name": name,
            "num": num,
            "routing_mark": f"out-{name}",
            "ip": ip_map.get(name, ""),
            "running": iface.get("running", "false") == "true",
            "disabled": iface.get("disabled", "false") == "true",
        })
    result.sort(key=lambda x: x["num"])
    return {"total": len(result), "proxies": result}


# ---------- Check Proxy (kiem tra proxy song/chet) ----------

@router.post("/mikrotik/check-proxies")
async def check_proxies(req: MikroTikConnectRequest, protocol: str = "http"):
    """Check proxy live/die — dung requests + ThreadPoolExecutor cho nhanh."""
    import re as _re
    import asyncio
    import json as _json
    import requests as _requests
    import urllib3 as _urllib3
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from starlette.responses import StreamingResponse

    _urllib3.disable_warnings()

    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        interfaces = client.list_pppoe_interfaces()
        creds_map = client.get_all_proxy_credentials()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    ip_map = {}
    try:
        for ip in client.list_ip_addresses():
            iface = ip.get("interface", "")
            addr = ip.get("address", "").split("/")[0]
            if iface:
                ip_map[iface] = addr
    except Exception:
        pass

    CHECK_URLS = [
        "http://api.ipify.org?format=json",
        "http://httpbin.org/ip",
        "http://ip-api.com/json",
    ]

    def _check_one_sync(index, entry, proxy_str):
        """Check 1 proxy bang requests (sync, chay trong thread)."""
        proxies_dict = {
            "http": f"http://{proxy_str}",
            "https": f"http://{proxy_str}",
        }
        if protocol == "socks":
            proxies_dict = {
                "http": f"socks5://{proxy_str}",
                "https": f"socks5://{proxy_str}",
            }

        for url in CHECK_URLS:
            try:
                resp = _requests.get(
                    url, proxies=proxies_dict, timeout=10,
                    verify=False, allow_redirects=True,
                )
                if resp.status_code == 200:
                    body = resp.json()
                    ip_val = body.get("ip", body.get("origin", body.get("query", "")))
                    if ip_val and "," in ip_val:
                        ip_val = ip_val.split(",")[0].strip()
                    entry["status"] = "alive"
                    entry["external_ip"] = ip_val or resp.text.strip()[:40]
                    return index, entry
            except Exception:
                continue

        entry["status"] = "dead"
        entry["error"] = "All check URLs failed"
        return index, entry

    entries = []
    check_jobs = []

    for idx, iface in enumerate(interfaces, 1):
        name = iface.get("name", "")
        running = iface.get("running", "false") == "true"
        disabled = iface.get("disabled", "false") == "true"

        num_match = _re.search(r"(\d+)$", name)
        num = int(num_match.group(1)) if num_match else idx
        http_port = 10000 + num
        socks_port = 20000 + num

        cred = creds_map.get(num, {})
        proxy_login = cred.get("proxy_login", "")
        proxy_password = cred.get("proxy_password", "")
        local_ip = ip_map.get(name, "")

        entry = {
            "name": name, "stt": idx,
            "http_port": http_port, "socks_port": socks_port,
            "proxy_login": proxy_login, "proxy_password": proxy_password,
            "local_ip": local_ip,
            "disabled": disabled, "running": running,
            "status": "pending", "external_ip": "", "error": "",
        }
        entries.append(entry)

        if disabled or not running:
            entry["status"] = "dead"
            entry["error"] = "disabled" if disabled else "interface down"
            continue

        auth_part = f"{proxy_login}:{proxy_password}@" if proxy_login else ""
        port = socks_port if protocol == "socks" else http_port
        proxy_str = f"{auth_part}{req.host}:{port}"
        check_jobs.append((len(entries) - 1, entry, proxy_str))

    total_count = len(entries)
    skip_count = total_count - len(check_jobs)
    result_queue = asyncio.Queue()
    _loop = asyncio.get_event_loop()

    def _run_checks_blocking():
        """Chay trong thread rieng, ko block event loop."""
        with ThreadPoolExecutor(max_workers=200) as pool:
            futures = {
                pool.submit(_check_one_sync, idx, ent, ps): idx
                for idx, ent, ps in check_jobs
            }
            for future in as_completed(futures):
                try:
                    res_idx, res_entry = future.result()
                    _loop.call_soon_threadsafe(result_queue.put_nowait, (res_idx, res_entry))
                except Exception:
                    pass
        _loop.call_soon_threadsafe(result_queue.put_nowait, None)

    async def _run_checks():
        await _loop.run_in_executor(None, _run_checks_blocking)

    async def _stream():
        yield ": padding" + " " * 2048 + "\n\n"
        yield f"data: {_json.dumps({'type':'progress','done':skip_count,'total':total_count})}\n\n"

        for i, e in enumerate(entries):
            if e["status"] == "dead" and e["error"]:
                yield f"data: {_json.dumps({'type':'item','index':i,'entry':e})}\n\n"

        check_task = asyncio.ensure_future(_run_checks())
        received = 0
        while True:
            try:
                item = await asyncio.wait_for(result_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                yield ": hb\n\n"
                continue
            if item is None:
                break
            idx, entry = item
            received += 1
            yield f"data: {_json.dumps({'type':'item','index':idx,'entry':entry})}\n\n"
            yield f"data: {_json.dumps({'type':'progress','done':received + skip_count,'total':total_count})}\n\n"

        await check_task
        alive = sum(1 for r in entries if r["status"] == "alive")
        yield f"data: {_json.dumps({'type':'result','total':total_count,'alive':alive,'dead':total_count - alive,'host':req.host,'results':entries})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------- Check External Proxy (proxy bat ky) ----------

from pydantic import BaseModel as _BaseModel

class ExternalProxyCheckRequest(_BaseModel):
    proxies: list[str] = []
    proxy_type: str = "http"
    timeout: int = 10


@router.post("/check-external-proxies")
async def check_external_proxies(req: ExternalProxyCheckRequest):
    """Check proxy bat ky — requests + ThreadPoolExecutor, stream realtime."""
    import asyncio
    import json as _json
    import requests as _requests
    import urllib3 as _urllib3
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from starlette.responses import StreamingResponse

    _urllib3.disable_warnings()

    EXT_CHECK_URLS = [
        "http://api.ipify.org?format=json",
        "http://httpbin.org/ip",
        "http://ip-api.com/json",
    ]

    def _check_one_sync(index, entry, proxy_str, scheme):
        proxies_dict = {
            "http": f"{scheme}://{proxy_str}",
            "https": f"{scheme}://{proxy_str}",
        }
        for url in EXT_CHECK_URLS:
            try:
                resp = _requests.get(
                    url, proxies=proxies_dict, timeout=req.timeout,
                    verify=False, allow_redirects=True,
                )
                if resp.status_code == 200:
                    body = resp.json()
                    ip_val = body.get("ip", body.get("origin", body.get("query", "")))
                    if ip_val and "," in ip_val:
                        ip_val = ip_val.split(",")[0].strip()
                    entry["status"] = "alive"
                    entry["external_ip"] = ip_val or resp.text.strip()[:40]
                    return index, entry
            except Exception:
                continue
        entry["status"] = "dead"
        entry["error"] = "All check URLs failed"
        return index, entry

    entries = []
    check_jobs = []
    for line in req.proxies:
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 2:
            entries.append({"proxy": line, "status": "dead",
                            "external_ip": "", "error": "Invalid format"})
            continue

        host_p = parts[0]
        port = parts[1]
        user = parts[2] if len(parts) > 2 else ""
        pwd = parts[3] if len(parts) > 3 else ""

        auth_part = f"{user}:{pwd}@" if user else ""
        scheme = "socks5" if req.proxy_type == "socks5" else "http"
        proxy_str = f"{auth_part}{host_p}:{port}"

        entry = {"proxy": line, "status": "pending", "external_ip": "", "error": ""}
        entries.append(entry)
        check_jobs.append((len(entries) - 1, entry, proxy_str, scheme))

    total_count = len(entries)
    skip_count = total_count - len(check_jobs)
    result_queue = asyncio.Queue()
    _loop = asyncio.get_event_loop()

    def _run_blocking():
        with ThreadPoolExecutor(max_workers=200) as pool:
            futures = {
                pool.submit(_check_one_sync, idx, ent, ps, sc): idx
                for idx, ent, ps, sc in check_jobs
            }
            for future in as_completed(futures):
                try:
                    res_idx, res_entry = future.result()
                    _loop.call_soon_threadsafe(result_queue.put_nowait, (res_idx, res_entry))
                except Exception:
                    pass
        _loop.call_soon_threadsafe(result_queue.put_nowait, None)

    async def _run_checks():
        await _loop.run_in_executor(None, _run_blocking)

    async def _stream():
        yield ": padding" + " " * 2048 + "\n\n"
        yield f"data: {_json.dumps({'type':'progress','done':0,'total':total_count})}\n\n"

        check_task = asyncio.ensure_future(_run_checks())
        received = 0
        while True:
            try:
                item = await asyncio.wait_for(result_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                yield ": hb\n\n"
                continue
            if item is None:
                break
            idx, entry = item
            received += 1
            yield f"data: {_json.dumps({'type':'item','index':idx,'entry':entry})}\n\n"
            yield f"data: {_json.dumps({'type':'progress','done':received + skip_count,'total':total_count})}\n\n"

        await check_task
        alive = sum(1 for r in entries if r["status"] == "alive")
        yield f"data: {_json.dumps({'type':'result','total':total_count,'alive':alive,'dead':total_count - alive,'results':entries})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------- Dashboard ----------

@router.get("/dashboard/stats", response_model=DashboardStats)
async def dashboard_stats(_user: dict = Depends(get_current_user)):
    db = get_db()
    proxies = db.collection("proxies").get()
    total = len(proxies)
    active = sum(1 for p in proxies if p.to_dict().get("status") == "online")
    return DashboardStats(total_proxy=total, active_proxy=active, offline_proxy=total - active)


# ---------- Proxy CRUD ----------

@router.get("/proxies", response_model=list[ProxyResponse])
async def list_proxies(
    search: str = Query("", description="Tim kiem IP"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _user: dict = Depends(get_current_user),
):
    db = get_db()
    docs = db.collection("proxies").get()
    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        if search and search not in data.get("ip", ""):
            continue
        results.append(ProxyResponse(**data))
    start = (page - 1) * limit
    return results[start:start + limit]


@router.post("/proxies", response_model=ProxyResponse)
async def create_proxy(req: ProxyCreate, _user: dict = Depends(get_current_user)):
    db = get_db()
    proxy_data = req.model_dump()
    proxy_data["status"] = "online"
    proxy_data["assigned_user"] = ""
    proxy_data["task"] = ""
    proxy_data["created_at"] = datetime.now(timezone.utc).isoformat()
    _, doc_ref = db.collection("proxies").add(proxy_data)
    proxy_data["id"] = doc_ref.id
    return ProxyResponse(**proxy_data)


@router.delete("/proxies/{proxy_id}")
async def delete_proxy(proxy_id: str, _user: dict = Depends(get_current_user)):
    db = get_db()
    doc = db.collection("proxies").document(proxy_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Proxy khong ton tai")
    db.collection("proxies").document(proxy_id).delete()
    return {"status": "ok"}


# ---------- Gan Proxy ----------

@router.post("/proxy-assignments")
async def assign_proxy(req: ProxyAssignRequest, _user: dict = Depends(get_current_user)):
    db = get_db()
    doc_ref = db.collection("proxies").document(req.proxy_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Proxy khong ton tai")
    doc_ref.update({"assigned_user": req.assigned_user, "task": req.task})
    return {"status": "ok", "proxy_id": req.proxy_id}


# ---------- Xoay IP ----------

@router.post("/mikrotik/rotate-ip")
async def rotate_single_ip(req: MikroTikConnectRequest, interface_name: str = "pppoe-out1"):
    """Xoay IP 1 interface: disable -> enable -> poll cho den khi co IP moi -> check trung."""
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        result = client.rotate_single(interface_name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/mikrotik/rotate-all-ips")
async def rotate_all_ips(req: MikroTikConnectRequest):
    """Xoay IP tat ca PPPoE: disable all -> enable all -> poll -> check trung lap."""
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        result = client.rotate_all()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/mikrotik/schedule-rotate")
async def schedule_rotate(req: MikroTikConnectRequest, interface_name: str = ""):
    """Xoay IP cho hen gio: disable 10s -> enable 1 lan, khong retry.
    interface_name rong = xoay tat ca, co gia tri = xoay 1 interface."""
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        if interface_name:
            result = client.rotate_single_simple(interface_name)
        else:
            result = client.rotate_all_simple()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


class ScheduleCreateRequest(_BaseModel):
    host: str
    username: str
    password: str
    interfaces: list[str] = []
    interval: str = "00:30:00"
    name: str = ""
    start_time: str = "startup"


@router.post("/mikrotik/schedule/create")
async def create_schedule(req: ScheduleCreateRequest):
    """Tao script + scheduler tren MikroTik de tu dong xoay PPPoE."""
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        if not req.interfaces:
            ifaces = client.list_pppoe_interfaces()
            req.interfaces = [i.get("name") for i in ifaces if i.get("name") and i.get("name") != "pppoe-out1"]
        result = client.create_rotate_schedule(
            interfaces=req.interfaces,
            interval=req.interval,
            name=req.name,
            start_time=req.start_time,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


class ScheduleDeleteRequest(_BaseModel):
    host: str
    username: str
    password: str
    interfaces: list[str] = []
    name: str = ""


@router.post("/mikrotik/schedule/delete")
async def delete_schedule(req: ScheduleDeleteRequest):
    """Xoa script + scheduler hen gio xoay tren MikroTik."""
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        result = client.remove_rotate_schedule(interfaces=req.interfaces, name=req.name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/mikrotik/schedule/list")
async def list_schedules(req: MikroTikConnectRequest):
    """Lay danh sach scheduler hen gio xoay tren MikroTik."""
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        result = client.get_rotate_schedules()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"schedules": result}


@router.post("/mikrotik/pppoe-status")
async def pppoe_live_status(req: MikroTikConnectRequest):
    """Lightweight: lay trang thai running + disabled + IP cua tat ca PPPoE (dung cho polling)."""
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    try:
        status = client.get_pppoe_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"clients": status}


@router.post("/mikrotik/disable-interfaces")
async def disable_interfaces(req: MikroTikConnectRequest, names: str = ""):
    """Disable 1 hoac nhieu PPPoE interface. names: comma-separated."""
    if not names:
        raise HTTPException(status_code=400, detail="Thieu names")
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    results = []
    for name in names.split(","):
        name = name.strip()
        if not name:
            continue
        try:
            client.disable_interface(name)
            results.append({"interface": name, "status": "disabled"})
        except Exception as e:
            results.append({"interface": name, "status": "error", "error": str(e)})
    return {"total": len(results), "results": results}


@router.post("/mikrotik/enable-interfaces")
async def enable_interfaces(req: MikroTikConnectRequest, names: str = ""):
    """Enable 1 hoac nhieu PPPoE interface. names: comma-separated."""
    if not names:
        raise HTTPException(status_code=400, detail="Thieu names")
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    results = []
    for name in names.split(","):
        name = name.strip()
        if not name:
            continue
        try:
            client.enable_interface(name)
            results.append({"interface": name, "status": "enabled"})
        except Exception as e:
            results.append({"interface": name, "status": "error", "error": str(e)})
    return {"total": len(results), "results": results}


# ---------- Lich su xoay IP ----------

@router.get("/rotation-history", response_model=list[RotationResponse])
async def rotation_history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _user: dict = Depends(get_current_user),
):
    db = get_db()
    docs = (
        db.collection("rotation_history")
        .order_by("rotated_at", direction="DESCENDING")
        .offset((page - 1) * limit)
        .limit(limit)
        .get()
    )
    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        results.append(RotationResponse(**data))
    return results


@router.get("/rotation-history/stats")
async def rotation_stats(_user: dict = Depends(get_current_user)):
    db = get_db()
    docs = db.collection("rotation_history").get()
    total = len(docs)
    success = sum(1 for d in docs if d.to_dict().get("status") == "success")
    return {
        "rotation_count_24h": total,
        "success_rate": round(success / total * 100, 1) if total else 100,
    }


# ---------- MikroTik resources truc tiep ----------

@router.get("/mikrotik/ppp-secrets")
async def mikrotik_ppp_secrets(user: dict = Depends(get_current_user)):
    return _get_mk(user).list_ppp_secrets()


@router.get("/mikrotik/ppp-active")
async def mikrotik_ppp_active(user: dict = Depends(get_current_user)):
    return _get_mk(user).list_ppp_active()


@router.get("/mikrotik/interfaces")
async def mikrotik_interfaces(user: dict = Depends(get_current_user)):
    return _get_mk(user).get_interfaces()


@router.get("/mikrotik/ip-addresses")
async def mikrotik_ip_addresses(user: dict = Depends(get_current_user)):
    return _get_mk(user).list_ip_addresses()


@router.get("/mikrotik/nat-rules")
async def mikrotik_nat_rules(user: dict = Depends(get_current_user)):
    return _get_mk(user).list_nat_rules()


# ---------- Settings: Ethernet / MACVLAN / PPPoE Config ----------

@router.post("/mikrotik/ethernet-status")
async def ethernet_status(req: MikroTikConnectRequest):
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    return {"ports": client.get_ethernet_status()}


@router.post("/mikrotik/macvlan-list")
async def macvlan_list(req: MikroTikConnectRequest):
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    raw = client.list_macvlan_interfaces()
    result = []
    for mv in raw:
        result.append({
            "id": mv.get("id", mv.get(".id", "")),
            "name": mv.get("name", ""),
            "interface": mv.get("interface", ""),
            "mac": mv.get("mac-address", ""),
            "mode": mv.get("mode", ""),
            "running": mv.get("running", "false") == "true",
            "disabled": mv.get("disabled", "false") == "true",
        })
    return {"macvlans": result}


@router.post("/mikrotik/pppoe-config")
async def pppoe_config(req: MikroTikConnectRequest):
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    return {"pppoe": client.get_pppoe_client_config()}


@router.post("/mikrotik/update-pppoe-creds")
async def update_pppoe_creds(
    req: MikroTikConnectRequest,
    pppoe_user: str = "",
    pppoe_password: str = "",
):
    if not pppoe_user and not pppoe_password:
        raise HTTPException(status_code=400, detail="Nhap user hoac password")
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    result = client.update_all_pppoe_credentials(user=pppoe_user, password=pppoe_password)
    return result


@router.post("/mikrotik/recreate-macvlan")
async def recreate_macvlan(
    req: MikroTikConnectRequest,
    ethernet: str = "",
    count: int = 0,
):
    if not ethernet or count < 2:
        raise HTTPException(status_code=400, detail="Nhap ethernet va count >= 2")
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    result = client.recreate_macvlans(interface=ethernet, count=count)
    return result


@router.post("/mikrotik/change-admin-password")
async def change_admin_password(
    req: MikroTikConnectRequest,
    username: str = "admin",
    new_password: str = "",
):
    if not new_password:
        raise HTTPException(status_code=400, detail="Nhap new_password")
    client = MikroTikClient(host=req.host, username=req.username, password=req.password)
    result = client.change_user_password(username=username, new_password=new_password)
    return result

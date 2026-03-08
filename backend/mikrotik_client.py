"""
MikroTik RouterOS API Client.
Port ẩn (hoặc lấy từ host:port). Host/user/pass do người dùng tự nhập.
Hỗ trợ IP public: timeout dài, host dạng http(s)://... hoặc host:port.
"""

import logging
import re
import socket
import time
from contextlib import contextmanager
from typing import Any

import routeros_api

from config import settings

logger = logging.getLogger(__name__)

DEFAULT_PORT = settings.MIKROTIK_PORT
TIMEOUT = getattr(settings, "MIKROTIK_TIMEOUT", 30.0)


def _normalize_host(host: str) -> tuple[str, int]:
    """Chuẩn hóa host: bỏ http(s)://; nếu có :port thì tách ra. Trả về (host, port)."""
    s = (host or "").strip()
    s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE)
    if ":" in s:
        parts = s.rsplit(":", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0].strip(), int(parts[1])
    return s, DEFAULT_PORT


class MikroTikClient:
    def __init__(self, host: str, username: str, password: str):
        self.host, self.port = _normalize_host(host)
        self.username = username
        self.password = password
        self._pool = None

    def _get_pool(self) -> routeros_api.RouterOsApiPool:
        if self._pool is None:
            self._pool = routeros_api.RouterOsApiPool(
                self.host,
                username=self.username,
                password=self.password,
                port=self.port,
                plaintext_login=settings.MIKROTIK_PLAINTEXT_LOGIN,
                use_ssl=settings.MIKROTIK_USE_SSL,
            )
            if TIMEOUT > 0:
                self._pool.set_timeout(TIMEOUT)
        return self._pool

    def _connect_with_retry(self, max_retries: int = 3):
        last_err = None
        for attempt in range(max_retries):
            try:
                pool = self._get_pool()
                api = pool.get_api()
                return api
            except (ConnectionResetError, ConnectionAbortedError, socket.error, OSError) as e:
                last_err = e
                self._pool = None
                if attempt < max_retries - 1:
                    time.sleep(1.0 + attempt * 0.5)
                    logger.warning("MikroTik retry %d/%d for %s: %s", attempt + 1, max_retries, self.host, e)
        raise last_err

    @contextmanager
    def connect(self):
        api = self._connect_with_retry()
        try:
            yield api
        finally:
            try:
                self._get_pool().disconnect()
            except Exception:
                pass
            self._pool = None

    # ---------- PPP Secrets (proxy users) ----------

    def list_ppp_secrets(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/ppp/secret").get()

    def add_ppp_secret(self, name: str, password: str, profile: str = "default", **kwargs) -> dict:
        with self.connect() as api:
            api.get_resource("/ppp/secret").add(name=name, password=password, profile=profile, **kwargs)
            return {"status": "ok", "name": name}

    def remove_ppp_secret(self, entry_id: str) -> dict:
        with self.connect() as api:
            api.get_resource("/ppp/secret").remove(id=entry_id)
            return {"status": "ok", "id": entry_id}

    def update_ppp_secret(self, entry_id: str, **kwargs) -> dict:
        with self.connect() as api:
            api.get_resource("/ppp/secret").set(id=entry_id, **kwargs)
            return {"status": "ok", "id": entry_id}

    # ---------- PPP Active ----------

    def list_ppp_active(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/ppp/active").get()

    # ---------- PPPoE ----------

    def list_pppoe_interfaces(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/interface/pppoe-client").get()

    def disable_interface(self, name: str):
        with self.connect() as api:
            api.get_resource("/interface").call("disable", {"numbers": name})

    def enable_interface(self, name: str):
        with self.connect() as api:
            api.get_resource("/interface").call("enable", {"numbers": name})

    # ---------- IP Address ----------

    def list_ip_addresses(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/ip/address").get()

    def get_ip_map(self) -> dict[str, str]:
        """Return {interface_name: ip_address}."""
        result = {}
        for ip in self.list_ip_addresses():
            iface = ip.get("interface", "")
            addr = ip.get("address", "").split("/")[0]
            if iface and addr:
                result[iface] = addr
        return result

    def get_pppoe_status(self) -> list[dict]:
        """Snapshot trang thai tat ca PPPoE: name, running, disabled, ip.
        MikroTik flags: R=running, X=disabled.
        """
        interfaces = self.list_pppoe_interfaces()
        ip_map = self.get_ip_map()
        result = []
        for iface in interfaces:
            name = iface.get("name", "")
            running = iface.get("running", "false") == "true"
            disabled = iface.get("disabled", "false") == "true"
            result.append({
                "name": name,
                "running": running,
                "disabled": disabled,
                "ip": ip_map.get(name, ""),
            })
        return result

    # ---------- Xoay IP ----------

    def rotate_single(self, interface_name: str, max_wait: int = 30, max_retry: int = 3) -> dict:
        """Xoay IP 1 interface: disable -> enable -> poll -> check duplicate."""
        if interface_name == "pppoe-out1":
            return {"interface": interface_name, "status": "blocked", "error": "pppoe-out1 la cong dieu khien, khong duoc xoay IP"}
        import time

        ip_map = self.get_ip_map()
        old_ip = ip_map.get(interface_name, "")
        other_ips = {v for k, v in ip_map.items() if k != interface_name and v}

        for attempt in range(1, max_retry + 1):
            self.disable_interface(interface_name)
            time.sleep(5)
            self.enable_interface(interface_name)

            new_ip = ""
            for _ in range(max_wait // 2):
                time.sleep(2)
                pppoe = self.get_pppoe_status()
                for p in pppoe:
                    if p["name"] == interface_name:
                        if p["running"] and p["ip"]:
                            new_ip = p["ip"]
                        break
                if new_ip:
                    break

            if not new_ip:
                logger.warning("rotate %s attempt %d: timeout, no IP", interface_name, attempt)
                continue

            if new_ip in other_ips:
                logger.warning("rotate %s attempt %d: duplicate IP %s", interface_name, attempt, new_ip)
                other_ips.discard(old_ip)
                continue

            return {
                "interface": interface_name,
                "old_ip": old_ip,
                "new_ip": new_ip,
                "status": "success",
                "attempts": attempt,
            }

        final_ip = self.get_ip_map().get(interface_name, "")
        return {
            "interface": interface_name,
            "old_ip": old_ip,
            "new_ip": final_ip,
            "status": "timeout" if not final_ip else ("duplicate" if final_ip in other_ips else "success"),
            "attempts": max_retry,
        }

    def rotate_all(self, max_wait: int = 40, max_retry: int = 3, skip: list[str] | None = None) -> dict:
        """Xoay IP tat ca PPPoE (bo qua skip list, mac dinh pppoe-out1)."""
        import time
        if skip is None:
            skip = ["pppoe-out1"]

        old_map = self.get_ip_map()
        interfaces = self.list_pppoe_interfaces()
        names = [i.get("name", "") for i in interfaces if i.get("name") and i.get("name") not in skip]

        for name in names:
            try:
                self.disable_interface(name)
            except Exception as e:
                logger.error("disable %s failed: %s", name, e)
        time.sleep(5)
        for name in names:
            try:
                self.enable_interface(name)
            except Exception as e:
                logger.error("enable %s failed: %s", name, e)

        for _ in range(max_wait // 3):
            time.sleep(3)
            status = self.get_pppoe_status()
            all_up = all(p["running"] and p["ip"] for p in status if p["name"] in names)
            if all_up:
                break

        ip_map = self.get_ip_map()
        results = []
        for name in names:
            new_ip = ip_map.get(name, "")
            results.append({
                "interface": name,
                "old_ip": old_map.get(name, ""),
                "new_ip": new_ip,
                "running": bool(new_ip),
            })

        seen_ips = {}
        duplicates = []
        for r in results:
            ip = r["new_ip"]
            if not ip:
                continue
            if ip in seen_ips:
                duplicates.append(r["interface"])
            else:
                seen_ips[ip] = r["interface"]

        retry_results = {}
        for dup_name in duplicates:
            for attempt in range(1, max_retry + 1):
                logger.info("rotate_all: re-rotating duplicate %s (attempt %d)", dup_name, attempt)
                res = self.rotate_single(dup_name, max_wait=20, max_retry=1)
                new_ip = res.get("new_ip", "")
                current_ips = {r["new_ip"] for r in results if r["interface"] != dup_name and r["new_ip"]}
                current_ips.update(v for k, v in retry_results.items() if k != dup_name and v)
                if new_ip and new_ip not in current_ips:
                    retry_results[dup_name] = new_ip
                    break
                retry_results[dup_name] = new_ip

        for r in results:
            if r["interface"] in retry_results:
                r["new_ip"] = retry_results[r["interface"]]
                r["running"] = bool(r["new_ip"])
                r["re_rotated"] = True

        all_ips = [r["new_ip"] for r in results if r["new_ip"]]
        has_dup = len(all_ips) != len(set(all_ips))

        return {
            "status": "success" if not has_dup else "has_duplicates",
            "total": len(results),
            "results": results,
        }

    def rotate_single_simple(self, interface_name: str, max_wait: int = 30) -> dict:
        """Xoay IP 1 interface đơn giản: disable 10s -> enable -> poll. Không retry."""
        if interface_name == "pppoe-out1":
            return {"interface": interface_name, "status": "blocked", "error": "pppoe-out1 không được xoay"}
        import time

        old_ip = self.get_ip_map().get(interface_name, "")

        try:
            self.disable_interface(interface_name)
        except Exception as e:
            logger.error("schedule disable %s failed: %s", interface_name, e)
        time.sleep(10)
        try:
            self.enable_interface(interface_name)
        except Exception as e:
            logger.error("schedule enable %s failed: %s", interface_name, e)

        new_ip = ""
        for _ in range(max_wait // 3):
            time.sleep(3)
            pppoe = self.get_pppoe_status()
            for p in pppoe:
                if p["name"] == interface_name and p["running"] and p["ip"]:
                    new_ip = p["ip"]
                    break
            if new_ip:
                break

        return {
            "interface": interface_name,
            "old_ip": old_ip,
            "new_ip": new_ip or self.get_ip_map().get(interface_name, ""),
            "status": "success" if new_ip else "timeout",
        }

    def rotate_all_simple(self, max_wait: int = 30, skip: list[str] | None = None) -> dict:
        """Xoay IP đơn giản: disable all -> wait 10s -> enable all -> poll IP. Không retry."""
        import time
        if skip is None:
            skip = ["pppoe-out1"]

        old_map = self.get_ip_map()
        interfaces = self.list_pppoe_interfaces()
        names = [i.get("name", "") for i in interfaces if i.get("name") and i.get("name") not in skip]

        for name in names:
            try:
                self.disable_interface(name)
            except Exception as e:
                logger.error("schedule disable %s failed: %s", name, e)
        time.sleep(10)
        for name in names:
            try:
                self.enable_interface(name)
            except Exception as e:
                logger.error("schedule enable %s failed: %s", name, e)

        for _ in range(max_wait // 3):
            time.sleep(3)
            status = self.get_pppoe_status()
            all_up = all(p["running"] and p["ip"] for p in status if p["name"] in names)
            if all_up:
                break

        ip_map = self.get_ip_map()
        results = []
        for name in names:
            new_ip = ip_map.get(name, "")
            results.append({
                "interface": name,
                "old_ip": old_map.get(name, ""),
                "new_ip": new_ip,
                "running": bool(new_ip),
            })

        return {
            "status": "success",
            "total": len(results),
            "results": results,
        }

    # ---------- DHCP Lease ----------

    def list_dhcp_leases(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/ip/dhcp-server/lease").get()

    # ---------- Firewall Mangle ----------

    def list_mangle_rules(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/ip/firewall/mangle").get()

    def add_mangle_rule(self, **kwargs) -> dict:
        with self.connect() as api:
            api.get_resource("/ip/firewall/mangle").add(**kwargs)
            return {"status": "ok"}

    def remove_mangle_rule(self, entry_id: str) -> dict:
        with self.connect() as api:
            api.get_resource("/ip/firewall/mangle").remove(id=entry_id)
            return {"status": "ok", "id": entry_id}

    def update_mangle_rule(self, entry_id: str, **kwargs) -> dict:
        with self.connect() as api:
            api.get_resource("/ip/firewall/mangle").set(id=entry_id, **kwargs)
            return {"status": "ok", "id": entry_id}

    # Prefix comment để đánh dấu rule do HT Proxy tạo
    HTPROXY_COMMENT = "htproxy"

    def find_mangle_for_src(self, src_address: str) -> dict | None:
        rules = self.list_mangle_rules()
        for r in rules:
            if (r.get("chain") == "prerouting"
                    and r.get("action") == "mark-routing"
                    and r.get("src-address") == src_address
                    and self.HTPROXY_COMMENT in (r.get("comment") or "")):
                return r
        return None

    def assign_proxy_mangle(self, src_address: str, routing_mark: str, comment: str = "") -> dict:
        """Create or update mangle rule: prerouting mark-routing for src_address."""
        # Luôn gắn prefix htproxy vào comment
        final_comment = self.HTPROXY_COMMENT
        if comment:
            final_comment = f"{self.HTPROXY_COMMENT} {comment}"

        existing = self.find_mangle_for_src(src_address)
        if existing:
            eid = existing.get("id", existing.get(".id", ""))
            params = {"new-routing-mark": routing_mark, "comment": final_comment}
            self.update_mangle_rule(eid, **params)
            return {"status": "updated", "id": eid, "src_address": src_address, "routing_mark": routing_mark}
        else:
            params = {
                "chain": "prerouting",
                "src-address": src_address,
                "action": "mark-routing",
                "new-routing-mark": routing_mark,
                "comment": final_comment,
            }
            self.add_mangle_rule(**params)
            return {"status": "created", "src_address": src_address, "routing_mark": routing_mark}

    def unassign_proxy_mangle(self, src_address: str) -> dict:
        existing = self.find_mangle_for_src(src_address)
        if not existing:
            return {"status": "not_found", "src_address": src_address}
        eid = existing.get("id", existing.get(".id", ""))
        self.remove_mangle_rule(eid)
        return {"status": "removed", "id": eid, "src_address": src_address}

    # ---------- Firewall NAT ----------

    def list_nat_rules(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/ip/firewall/nat").get()

    def add_nat_rule(self, **kwargs) -> dict:
        with self.connect() as api:
            api.get_resource("/ip/firewall/nat").add(**kwargs)
            return {"status": "ok"}

    def remove_nat_rule(self, entry_id: str) -> dict:
        with self.connect() as api:
            api.get_resource("/ip/firewall/nat").remove(id=entry_id)
            return {"status": "ok", "id": entry_id}

    # ---------- System ----------

    def get_system_identity(self) -> dict:
        with self.connect() as api:
            return api.get_resource("/system/identity").get()[0]

    def get_system_resource(self) -> dict:
        with self.connect() as api:
            return api.get_resource("/system/resource").get()[0]

    def get_interfaces(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/interface").get()

    # ---------- Script & Scheduler ----------

    def list_scripts(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/system/script").get()

    def list_schedulers(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/system/scheduler").get()

    def create_rotate_schedule(self, interfaces: list[str], interval: str = "00:30:00",
                                name: str = "", start_time: str = "startup") -> dict:
        """Tạo script + scheduler trên MikroTik để tự disable/enable PPPoE.
        interval: RouterOS format (HH:MM:SS hoặc Xd HH:MM:SS)
        interfaces: danh sách pppoe-outX cần xoay (bỏ pppoe-out1)
        """
        safe_ifaces = [i for i in interfaces if i != "pppoe-out1"]
        if not safe_ifaces:
            return {"status": "error", "error": "Không có interface hợp lệ"}

        script_name = name or ("ht_rotate_" + "_".join(safe_ifaces))
        sched_name = "sched_" + script_name

        disable_cmds = "\n".join(
            f'/interface disable [find name="{iface}"]' for iface in safe_ifaces
        )
        enable_cmds = "\n".join(
            f'/interface enable [find name="{iface}"]' for iface in safe_ifaces
        )

        script_body = f"""{disable_cmds}
:delay 10s
{enable_cmds}"""

        with self.connect() as api:
            scripts = api.get_resource("/system/script")
            existing = [s for s in scripts.get() if s.get("name") == script_name]
            if existing:
                scripts.set(id=existing[0]["id"], source=script_body)
            else:
                scripts.add(name=script_name, source=script_body, policy="read,write,test")

            schedulers = api.get_resource("/system/scheduler")
            existing_sched = [s for s in schedulers.get() if s.get("name") == sched_name]
            sched_params = {
                "name": sched_name,
                "on-event": script_name,
                "interval": interval,
                "start-time": start_time,
                "policy": "read,write,test",
            }
            if existing_sched:
                schedulers.set(id=existing_sched[0]["id"], **{k: v for k, v in sched_params.items() if k != "name"})
            else:
                schedulers.add(**sched_params)

        return {
            "status": "success",
            "script_name": script_name,
            "scheduler_name": sched_name,
            "interfaces": safe_ifaces,
            "interval": interval,
        }

    def remove_rotate_schedule(self, interfaces: list[str] = None, name: str = "") -> dict:
        """Xóa script + scheduler hẹn giờ xoay."""
        script_name = name or ("ht_rotate_" + "_".join(interfaces or []))
        sched_name = "sched_" + script_name

        with self.connect() as api:
            schedulers = api.get_resource("/system/scheduler")
            for s in schedulers.get():
                if s.get("name") == sched_name:
                    schedulers.remove(id=s["id"])

            scripts = api.get_resource("/system/script")
            for s in scripts.get():
                if s.get("name") == script_name:
                    scripts.remove(id=s["id"])

        return {"status": "success", "removed_script": script_name, "removed_scheduler": sched_name}

    def get_rotate_schedules(self) -> list[dict]:
        """Lấy danh sách scheduler hẹn giờ xoay (bắt đầu bằng sched_ht_rotate_)."""
        result = []
        schedulers = self.list_schedulers()
        scripts = {s.get("name"): s for s in self.list_scripts()}
        for s in schedulers:
            name = s.get("name", "")
            if not name.startswith("sched_ht_rotate_"):
                continue
            script_name = name.replace("sched_", "", 1)
            script = scripts.get(script_name, {})
            result.append({
                "scheduler_id": s.get("id"),
                "scheduler_name": name,
                "script_name": script_name,
                "interval": s.get("interval", ""),
                "start_time": s.get("start-time", ""),
                "next_run": s.get("next-run", ""),
                "run_count": s.get("run-count", "0"),
                "source": script.get("source", ""),
            })
        return result

    # ---------- Socks Proxy ----------

    def get_socks_settings(self) -> dict:
        with self.connect() as api:
            items = api.get_resource("/ip/socks").get()
            return items[0] if items else {}

    def list_socks_users(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/ip/socks/users").get()

    # ---------- Web Proxy ----------

    def get_web_proxy(self) -> dict:
        with self.connect() as api:
            items = api.get_resource("/ip/proxy").get()
            return items[0] if items else {}

    # ---------- Container ----------

    def list_containers(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/container").get()

    def stop_container(self, container_id: str) -> dict:
        with self.connect() as api:
            api.get_resource("/container").call("stop", {".id": container_id})
            return {"status": "ok", "action": "stop", "id": container_id}

    def start_container(self, container_id: str) -> dict:
        with self.connect() as api:
            api.get_resource("/container").call("start", {".id": container_id})
            return {"status": "ok", "action": "start", "id": container_id}

    def _get_container_status(self, container_id: str) -> str:
        containers = self.list_containers()
        for ct in containers:
            cid = ct.get("id", ct.get(".id", ""))
            if cid == container_id:
                return ct.get("status", "")
        return ""

    def restart_container(self, container_id: str) -> dict:
        """Stop -> chờ 5s -> start -> poll tối đa 30s đảm bảo running."""
        import time
        self.stop_container(container_id)
        time.sleep(5)
        self.start_container(container_id)

        for attempt in range(6):
            time.sleep(5)
            status = self._get_container_status(container_id)
            logger.info("restart poll #%d container=%s status=%s", attempt + 1, container_id, status)
            if status == "running":
                return {"status": "ok", "action": "restarted", "id": container_id, "container_status": "running"}

        final = self._get_container_status(container_id)
        if final != "running":
            self.start_container(container_id)
            time.sleep(5)
            final = self._get_container_status(container_id)
        return {"status": "ok", "action": "restarted", "id": container_id, "container_status": final}

    # ---------- Container Envs ----------

    def list_container_envs(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/container/envs").get()

    def update_container_env(self, env_id: str, value: str) -> dict:
        with self.connect() as api:
            api.get_resource("/container/envs").set(id=env_id, value=value)
            return {"status": "ok", "id": env_id, "value": value}

    def change_proxy_credentials(self, envlist: str, login: str, pwd: str) -> dict:
        """Update PROXY_LOGIN va PROXY_PASSWORD cho 1 envlist cu the."""
        envs = self.list_container_envs()
        updated = []
        for env in envs:
            if env.get("name") != envlist:
                continue
            key = env.get("key", "")
            env_id = env.get("id", env.get(".id", ""))
            if key == "PROXY_LOGIN":
                self.update_container_env(env_id, login)
                updated.append({"key": key, "new_value": login})
            elif key == "PROXY_PASSWORD":
                self.update_container_env(env_id, pwd)
                updated.append({"key": key, "new_value": pwd})
        return {"envlist": envlist, "updated": updated}

    def get_all_proxy_credentials(self) -> dict:
        """Lay proxy credentials tu container + envlist.
        Return: {pppoe_num: {login, password, container_id, envlist, veth_interface}}
        """
        containers = self.list_containers()
        envs = self.list_container_envs()

        envs_by_list = {}
        for env in envs:
            list_name = env.get("name", "")
            key = env.get("key", "")
            val = env.get("value", "")
            if list_name:
                if list_name not in envs_by_list:
                    envs_by_list[list_name] = {}
                envs_by_list[list_name][key] = val

        import re
        result = {}
        for ct in containers:
            ct_id = ct.get("id", ct.get(".id", ""))
            veth = ct.get("interface", "")
            envlist = ct.get("envlist", "")
            status = ct.get("status", "")

            m = re.search(r"envs(\d+)", envlist)
            if not m:
                continue
            num = int(m.group(1))

            creds = envs_by_list.get(envlist, {})
            result[num] = {
                "proxy_login": creds.get("PROXY_LOGIN", ""),
                "proxy_password": creds.get("PROXY_PASSWORD", ""),
                "container_id": ct_id,
                "envlist": envlist,
                "veth_interface": veth,
                "container_status": status,
            }
        return result

    # ---------- Ethernet Ports ----------

    def list_ethernet_interfaces(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/interface/ethernet").get()

    def get_ethernet_status(self) -> list[dict]:
        ifaces = self.list_ethernet_interfaces()
        result = []
        for iface in ifaces:
            name = iface.get("name", "")
            running = iface.get("running", "false") == "true"
            disabled = iface.get("disabled", "false") == "true"
            speed = iface.get("speed", "")
            mac = iface.get("mac-address", "")
            result.append({
                "name": name,
                "running": running,
                "disabled": disabled,
                "speed": speed,
                "mac": mac,
            })
        return result

    # ---------- MACVLAN ----------

    def list_macvlan_interfaces(self) -> list[dict[str, Any]]:
        with self.connect() as api:
            return api.get_resource("/interface/macvlan").get()

    def add_macvlan(self, name: str, interface: str, mac_address: str = "", mode: str = "private") -> dict:
        with self.connect() as api:
            params = {"name": name, "interface": interface, "mode": mode}
            if mac_address:
                params["mac-address"] = mac_address
            api.get_resource("/interface/macvlan").add(**params)
            return {"status": "ok", "name": name}

    def remove_macvlan(self, entry_id: str) -> dict:
        with self.connect() as api:
            api.get_resource("/interface/macvlan").remove(id=entry_id)
            return {"status": "ok", "id": entry_id}

    def recreate_macvlans(self, interface: str, count: int, mode: str = "private") -> dict:
        """Delete all macvlan except macvlan1, then recreate macvlan2..macvlan{count} on the given ethernet interface."""
        import random
        existing = self.list_macvlan_interfaces()
        removed = 0
        for mv in existing:
            name = mv.get("name", "")
            if name == "macvlan1":
                continue
            eid = mv.get("id", mv.get(".id", ""))
            if eid:
                try:
                    self.remove_macvlan(eid)
                    removed += 1
                except Exception as e:
                    logger.warning("Failed to remove macvlan %s: %s", name, e)

        created = 0
        for i in range(2, count + 1):
            name = f"macvlan{i}"
            mac = "4E:0C:{:02X}:{:02X}:{:02X}:{:02X}".format(
                random.randint(0, 255), random.randint(0, 255),
                random.randint(0, 255), random.randint(0, 255),
            )
            try:
                self.add_macvlan(name=name, interface=interface, mac_address=mac, mode=mode)
                created += 1
            except Exception as e:
                logger.warning("Failed to create %s: %s", name, e)

        reassigned = self.reassign_pppoe_interfaces()

        return {"removed": removed, "created": created, "total": count, "reassigned": reassigned}

    def reassign_pppoe_interfaces(self) -> list[str]:
        """Reassign pppoe-outN to macvlanN (pppoe-out1 stays on macvlan1)."""
        macvlan_names = {mv.get("name") for mv in self.list_macvlan_interfaces()}
        updated = []
        with self.connect() as api:
            pppoe = api.get_resource("/interface/pppoe-client")
            items = pppoe.get()
            for item in items:
                name = item.get("name", "")
                if name == "pppoe-out1":
                    continue
                num = name.replace("pppoe-out", "")
                target_mv = f"macvlan{num}"
                current_iface = item.get("interface", "")
                if target_mv in macvlan_names and current_iface != target_mv:
                    eid = item.get("id", item.get(".id", ""))
                    try:
                        pppoe.set(id=eid, interface=target_mv)
                        updated.append(f"{name} → {target_mv}")
                    except Exception as e:
                        logger.warning("Failed to reassign %s to %s: %s", name, target_mv, e)
        return updated

    # ---------- PPPoE Client Config ----------

    def get_pppoe_client_config(self) -> list[dict]:
        """Get PPPoE client interface configs with user/password/interface."""
        ifaces = self.list_pppoe_interfaces()
        result = []
        for iface in ifaces:
            result.append({
                "name": iface.get("name", ""),
                "user": iface.get("user", ""),
                "password": iface.get("password", ""),
                "interface": iface.get("interface", ""),
                "disabled": iface.get("disabled", "false") == "true",
                "running": iface.get("running", "false") == "true",
                "id": iface.get("id", iface.get(".id", "")),
            })
        return result

    def update_pppoe_credentials(self, interface_name: str, user: str, password: str) -> dict:
        """Update PPPoE user/password for a specific pppoe-out interface."""
        with self.connect() as api:
            pppoe = api.get_resource("/interface/pppoe-client")
            items = pppoe.get()
            for item in items:
                if item.get("name") == interface_name:
                    eid = item.get("id", item.get(".id", ""))
                    pppoe.set(id=eid, user=user, password=password)
                    return {"status": "ok", "name": interface_name}
            return {"status": "not_found", "name": interface_name}

    def update_all_pppoe_credentials(self, user: str, password: str, skip: list[str] | None = None) -> dict:
        """Update PPPoE user/password for all interfaces except skip list."""
        if skip is None:
            skip = ["pppoe-out1"]
        with self.connect() as api:
            pppoe = api.get_resource("/interface/pppoe-client")
            items = pppoe.get()
            updated = []
            for item in items:
                name = item.get("name", "")
                if name in skip:
                    continue
                eid = item.get("id", item.get(".id", ""))
                pppoe.set(id=eid, user=user, password=password)
                updated.append(name)
        return {"status": "ok", "updated": updated, "count": len(updated)}

    def change_user_password(self, username: str = "admin", new_password: str = "") -> dict:
        with self.connect() as api:
            users = api.get_resource("/user")
            items = users.get()
            for item in items:
                if item.get("name") == username:
                    eid = item.get("id", item.get(".id", ""))
                    users.set(id=eid, password=new_password)
                    return {"status": "ok", "username": username, "host": self.host}
            return {"status": "not_found", "username": username}

    # ---------- Utility ----------

    def test_connection(self) -> dict:
        try:
            identity = self.get_system_identity()
            resource = self.get_system_resource()
            total_mem = int(resource.get("total-memory", 0))
            free_mem = int(resource.get("free-memory", 0))
            total_hdd = int(resource.get("total-hdd-space", 0))
            free_hdd = int(resource.get("free-hdd-space", 0))
            return {
                "connected": True,
                "identity": identity.get("name", "unknown"),
                "version": resource.get("version", "unknown"),
                "uptime": resource.get("uptime", "unknown"),
                "cpu_load": resource.get("cpu-load", "0"),
                "cpu_count": resource.get("cpu-count", "1"),
                "cpu_freq": resource.get("cpu-frequency", "0"),
                "board": resource.get("board-name", ""),
                "arch": resource.get("architecture-name", ""),
                "total_memory": total_mem,
                "free_memory": free_mem,
                "used_memory": total_mem - free_mem,
                "total_hdd": total_hdd,
                "free_hdd": free_hdd,
                "used_hdd": total_hdd - free_hdd,
            }
        except Exception as e:
            logger.error(f"MikroTik connection failed: {e}")
            return {"connected": False, "error": str(e)}


def get_mikrotik_for_user(user: dict) -> MikroTikClient:
    """Tao MikroTikClient tu credentials luu trong Firestore cua user."""
    mk = user.get("mikrotik", {})
    host = mk.get("host", "")
    username = mk.get("username", "")
    password = mk.get("password", "")
    if not host or not username:
        raise ValueError("Chua cau hinh ket noi MikroTik. Vui long ket noi truoc.")
    return MikroTikClient(host=host, username=username, password=password)

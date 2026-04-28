import asyncio
import json
import subprocess
import time
from pathlib import Path

import httpx

from app.core.config import Settings
from app.schemas import ServiceActionResponse, ServiceOverview, ServiceStatus


class ServiceMonitor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pid_by_port: dict[int, int] = {}
        self._process_name_by_pid: dict[int, str] = {}

    async def overview(self) -> ServiceOverview:
        self._pid_by_port = self._pids_for_ports(
            [
                self.settings.api_port,
                self.settings.admin_frontend_port,
                self.settings.comfyui_port,
                self.settings.ollama_port,
            ]
        )
        self._process_name_by_pid = self._process_names_for_pids(
            [pid for pid in self._pid_by_port.values() if pid]
        )
        api_status, frontend_status, comfyui_status, ollama_status, telegram_status = await asyncio.gather(
            self._api_status(),
            self._frontend_status(),
            self._comfyui_status(),
            self._ollama_status(),
            self._telegram_status(),
        )
        running, pending = await self._comfyui_queue_counts()
        return ServiceOverview(
            services=[
                api_status,
                frontend_status,
                comfyui_status,
                ollama_status,
                telegram_status,
            ],
            queue_running=running,
            queue_pending=pending,
        )

    async def start(self, service: str) -> ServiceActionResponse:
        if service != "comfyui":
            return ServiceActionResponse(
                service=service,
                action="start",
                ok=False,
                message="Only ComfyUI can be started from the backend control API.",
            )

        port_pid = self._pid_for_port(self.settings.comfyui_port)
        if port_pid:
            return ServiceActionResponse(
                service=service,
                action="start",
                ok=True,
                message=f"ComfyUI is already running on port {self.settings.comfyui_port}.",
            )

        root = Path(self.settings.comfyui_root)
        script = root / self.settings.comfyui_start_script
        if not root.exists() or not script.exists():
            return ServiceActionResponse(
                service=service,
                action="start",
                ok=False,
                message=f"ComfyUI start script not found: {script}",
            )

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["cmd.exe", "/c", script.name],
            cwd=root,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return ServiceActionResponse(
            service=service,
            action="start",
            ok=True,
            message=f"ComfyUI start requested on port {self.settings.comfyui_port}.",
        )

    async def stop(self, service: str) -> ServiceActionResponse:
        if service != "comfyui":
            return ServiceActionResponse(
                service=service,
                action="stop",
                ok=False,
                message="Only ComfyUI can be stopped from the backend control API.",
            )

        pid = self._pid_for_port(self.settings.comfyui_port)
        if not pid:
            return ServiceActionResponse(
                service=service,
                action="stop",
                ok=True,
                message="ComfyUI is not running.",
            )

        command_line = self._command_line_for_pid(pid)
        comfy_root = str(Path(self.settings.comfyui_root)).lower()
        if comfy_root and comfy_root not in command_line.lower() and "comfyui" not in command_line.lower():
            return ServiceActionResponse(
                service=service,
                action="stop",
                ok=False,
                message=f"Refusing to stop PID {pid}; it does not look like configured ComfyUI.",
            )

        self._stop_pid(pid)
        return ServiceActionResponse(
            service=service,
            action="stop",
            ok=True,
            message=f"Stopped ComfyUI process {pid}.",
        )

    async def restart(self, service: str) -> ServiceActionResponse:
        stopped = await self.stop(service)
        if not stopped.ok:
            return ServiceActionResponse(
                service=service,
                action="restart",
                ok=False,
                message=stopped.message,
            )
        await asyncio.sleep(2)
        started = await self.start(service)
        return ServiceActionResponse(
            service=service,
            action="restart",
            ok=started.ok,
            message=started.message,
        )

    async def _api_status(self) -> ServiceStatus:
        pid = self._pid_for_port(self.settings.api_port)
        return ServiceStatus(
            key="api",
            name="VibeVision API",
            status="online",
            url=self.settings.api_base_url,
            port=self.settings.api_port,
            pid=pid,
            process_name=self._process_name_for_pid(pid),
            detail="Serving admin API and bot webhook.",
        )

    async def _frontend_status(self) -> ServiceStatus:
        url = f"http://{self.settings.admin_frontend_host}:{self.settings.admin_frontend_port}"
        return await self._http_status(
            key="frontend",
            name="Admin Frontend",
            url=url,
            port=self.settings.admin_frontend_port,
            endpoint="/",
            detail_online="Admin workspace is reachable.",
            detail_offline="Admin frontend dev server is not reachable.",
        )

    async def _comfyui_status(self) -> ServiceStatus:
        status = await self._http_status(
            key="comfyui",
            name="ComfyUI",
            url=self.settings.comfyui_base_url,
            port=self.settings.comfyui_port,
            endpoint="/system_stats",
            detail_online=f"Backend root: {self.settings.comfyui_root}",
            detail_offline="ComfyUI HTTP backend is not reachable.",
        )
        status.can_start = True
        status.can_stop = status.status == "online"
        return status

    async def _ollama_status(self) -> ServiceStatus:
        return await self._http_status(
            key="ollama",
            name="Ollama",
            url=self.settings.ollama_base_url,
            port=self.settings.ollama_port,
            endpoint="/api/tags",
            detail_online=f"Model: {self.settings.ollama_model}",
            detail_offline="Ollama is not reachable.",
        )

    async def _telegram_status(self) -> ServiceStatus:
        configured = bool(self.settings.telegram_bot_token)
        return ServiceStatus(
            key="telegram",
            name="Telegram Bot",
            status="configured" if configured else "unconfigured",
            detail="Bot token is configured." if configured else "Set TELEGRAM_BOT_TOKEN in local config.",
            can_start=False,
            can_stop=False,
        )

    async def _http_status(
        self,
        key: str,
        name: str,
        url: str,
        port: int,
        endpoint: str,
        detail_online: str,
        detail_offline: str,
    ) -> ServiceStatus:
        pid = self._pid_for_port(port)
        try:
            started = time.perf_counter()
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{url}{endpoint}")
                response.raise_for_status()
            latency = int((time.perf_counter() - started) * 1000)
            return ServiceStatus(
                key=key,
                name=name,
                status="online",
                url=url,
                port=port,
                pid=pid,
                process_name=self._process_name_for_pid(pid),
                detail=detail_online,
                latency_ms=latency,
            )
        except Exception as exc:
            return ServiceStatus(
                key=key,
                name=name,
                status="offline",
                url=url,
                port=port,
                pid=pid,
                process_name=self._process_name_for_pid(pid),
                detail=f"{detail_offline} {exc}",
            )

    async def _comfyui_queue_counts(self) -> tuple[int, int]:
        try:
            async with httpx.AsyncClient(base_url=self.settings.comfyui_base_url, timeout=3) as client:
                response = await client.get("/queue")
                response.raise_for_status()
                body = response.json()
            return len(body.get("queue_running") or []), len(body.get("queue_pending") or [])
        except Exception:
            return 0, 0

    def _pid_for_port(self, port: int) -> int | None:
        if port in self._pid_by_port:
            return self._pid_by_port[port]
        script = (
            f"$c=Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
            "| Select-Object -First 1 -ExpandProperty OwningProcess; "
            "if ($c) { Write-Output $c }"
        )
        output = self._powershell(script)
        try:
            return int(output.strip()) if output.strip() else None
        except ValueError:
            return None

    def _pids_for_ports(self, ports: list[int]) -> dict[int, int]:
        port_list = ",".join(str(port) for port in sorted(set(ports)))
        script = (
            f"$ports=@({port_list}); "
            "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue "
            "| Where-Object { $ports -contains $_.LocalPort } "
            "| Sort-Object LocalPort "
            "| Select-Object LocalPort,OwningProcess "
            "| ConvertTo-Json -Compress"
        )
        output = self._powershell(script).strip()
        if not output:
            return {}
        try:
            rows = json.loads(output)
        except json.JSONDecodeError:
            return {}
        if isinstance(rows, dict):
            rows = [rows]
        return {
            int(row["LocalPort"]): int(row["OwningProcess"])
            for row in rows
            if row.get("LocalPort") and row.get("OwningProcess")
        }

    def _process_name_for_pid(self, pid: int | None) -> str | None:
        if not pid:
            return None
        return self._process_name_by_pid.get(pid)

    def _process_names_for_pids(self, pids: list[int]) -> dict[int, str]:
        pid_list = ",".join(str(pid) for pid in sorted(set(pids)))
        if not pid_list:
            return {}
        script = (
            f"$pids=@({pid_list}); "
            "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue "
            "| Where-Object { $pids -contains $_.ProcessId } "
            "| Select-Object ProcessId,Name "
            "| ConvertTo-Json -Compress"
        )
        output = self._powershell(script).strip()
        if not output:
            return {}
        try:
            rows = json.loads(output)
        except json.JSONDecodeError:
            return {}
        if isinstance(rows, dict):
            rows = [rows]
        return {
            int(row["ProcessId"]): str(row["Name"])
            for row in rows
            if row.get("ProcessId") and row.get("Name")
        }

    def _command_line_for_pid(self, pid: int) -> str:
        script = (
            f"Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" "
            "| Select-Object -ExpandProperty CommandLine"
        )
        return self._powershell(script).strip()

    def _stop_pid(self, pid: int) -> None:
        self._powershell(f"Stop-Process -Id {pid} -Force")

    def _powershell(self, script: str) -> str:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.stdout

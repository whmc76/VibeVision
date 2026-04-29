import asyncio
import csv
import io
import subprocess
import time
from pathlib import Path

import httpx

from app.core.config import Settings
from app.schemas import ServiceActionResponse, ServiceOverview, ServiceStatus
from app.services.error_details import format_exception_details
from app.services.telegram import TelegramClient, TelegramUpdateError


class ServiceMonitor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pid_by_port: dict[int, int] = {}
        self._process_name_by_pid: dict[int, str] = {}

    async def overview(self) -> ServiceOverview:
        monitored_ports = [
            self.settings.api_port,
            self.settings.admin_frontend_port,
            self.settings.comfyui_port,
        ]
        if self._llm_uses_ollama():
            monitored_ports.append(self.settings.ollama_port)
        self._pid_by_port = self._pids_for_ports(monitored_ports)
        self._process_name_by_pid = self._process_names_for_pids(
            [pid for pid in self._pid_by_port.values() if pid]
        )
        api_status, frontend_status, comfyui_status, telegram_status = await asyncio.gather(
            self._api_status(),
            self._frontend_status(),
            self._comfyui_status(),
            self._telegram_status(),
        )
        llm_statuses = await self._llm_statuses()
        running, pending = await self._comfyui_queue_counts()
        return ServiceOverview(
            services=[
                api_status,
                frontend_status,
                comfyui_status,
                *llm_statuses,
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
            detail_online=f"Models: {self.settings.ollama_model_summary}",
            detail_offline="Ollama is not reachable.",
        )

    async def _llm_statuses(self) -> list[ServiceStatus]:
        statuses: list[ServiceStatus] = []
        if self._llm_uses_minimax():
            configured = bool(self.settings.minimax_api_key)
            statuses.append(
                ServiceStatus(
                    key="llm",
                    name="MiniMax LLM",
                    status="configured" if configured else "unconfigured",
                    url=self.settings.minimax_base_url,
                    detail=(
                        f"Models: {self.settings.minimax_model_summary}"
                        if configured
                        else "Set MINIMAX_API_KEY in local config."
                    ),
                ),
            )
        if self._llm_uses_ollama():
            statuses.append(await self._ollama_status())
        return statuses

    def _llm_uses_minimax(self) -> bool:
        return (
            self.settings.llm_logic_provider_name == "minimax"
            or self.settings.llm_prompt_provider_name == "minimax"
            or self.settings.llm_vision_provider_name == "minimax_mcp"
        )

    def _llm_uses_ollama(self) -> bool:
        return (
            self.settings.llm_logic_provider_name == "ollama"
            or self.settings.llm_prompt_provider_name == "ollama"
            or self.settings.llm_vision_provider_name == "ollama"
        )

    async def _telegram_status(self) -> ServiceStatus:
        pid = self._pid_for_port(self.settings.api_port)
        poller_pid = self._telegram_poller_pid()
        if not self.settings.telegram_bot_token:
            return ServiceStatus(
                key="telegram",
                name="Telegram Bot",
                status="unconfigured",
                port=self.settings.api_port,
                pid=pid,
                process_name=self._process_name_for_pid(pid),
                detail="Set TELEGRAM_BOT_TOKEN in local config.",
                can_start=False,
                can_stop=False,
            )

        started = time.perf_counter()
        try:
            webhook = await TelegramClient(self.settings).get_webhook_info()
            latency = int((time.perf_counter() - started) * 1000)
        except TelegramUpdateError as exc:
            return ServiceStatus(
                key="telegram",
                name="Telegram Bot",
                status="offline",
                port=self.settings.api_port,
                pid=pid,
                process_name=self._process_name_for_pid(pid),
                detail=f"Telegram webhook check failed: {format_exception_details(exc)}",
                can_start=False,
                can_stop=False,
            )

        if not webhook.url:
            if poller_pid:
                return ServiceStatus(
                    key="telegram",
                    name="Telegram Bot",
                    status="configured",
                    url="local getUpdates polling",
                    port=self.settings.api_port,
                    pid=poller_pid,
                    process_name=self._process_name_for_pid(poller_pid),
                    detail="Local Telegram poller is running.",
                    latency_ms=latency,
                    can_start=False,
                    can_stop=False,
                )

            detail = "Bot token is configured, but no webhook URL is registered."
            if webhook.pending_update_count:
                detail += f" Pending updates: {webhook.pending_update_count}."
            return ServiceStatus(
                key="telegram",
                name="Telegram Bot",
                status="unconfigured",
                port=self.settings.api_port,
                pid=pid,
                process_name=self._process_name_for_pid(pid),
                detail=detail,
                latency_ms=latency,
                can_start=False,
                can_stop=False,
            )

        detail = "Webhook registered."
        if webhook.pending_update_count:
            detail += f" Pending updates: {webhook.pending_update_count}."
        if webhook.last_error_message:
            detail += f" Last error: {webhook.last_error_message}"

        return ServiceStatus(
            key="telegram",
            name="Telegram Bot",
            status="offline" if webhook.last_error_message else "configured",
            url=webhook.url,
            port=self.settings.api_port,
            pid=poller_pid or pid,
            process_name=self._process_name_for_pid(poller_pid or pid),
            detail=detail,
            latency_ms=latency,
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
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
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
                detail=f"{detail_offline} {format_exception_details(exc)}",
            )

    async def _comfyui_queue_counts(self) -> tuple[int, int]:
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.comfyui_base_url,
                timeout=3,
                trust_env=False,
            ) as client:
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
        target_ports = {int(port) for port in ports if port > 0}
        if not target_ports:
            return {}

        completed = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if completed.returncode != 0:
            return {}

        result: dict[int, int] = {}
        for line in completed.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0] != "TCP" or parts[-2] != "LISTENING":
                continue
            try:
                port = int(parts[1].rsplit(":", 1)[1])
                pid = int(parts[-1])
            except (IndexError, ValueError):
                continue
            if port in target_ports and port not in result:
                result[port] = pid
        return result

    def _process_name_for_pid(self, pid: int | None) -> str | None:
        if not pid:
            return None
        return self._process_name_by_pid.get(pid)

    def _telegram_poller_pid(self) -> int | None:
        script = (
            "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue "
            "| Where-Object { $_.CommandLine -like '*app.services.telegram_poller*' } "
            "| Select-Object -First 1 -ExpandProperty ProcessId"
        )
        output = self._powershell(script)
        try:
            return int(output.strip()) if output.strip() else None
        except ValueError:
            return None

    def _process_names_for_pids(self, pids: list[int]) -> dict[int, str]:
        target_pids = {int(pid) for pid in pids if pid}
        if not target_pids:
            return {}

        completed = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if completed.returncode != 0:
            return {}

        result: dict[int, str] = {}
        for row in csv.reader(io.StringIO(completed.stdout)):
            if len(row) < 2:
                continue
            try:
                pid = int(row[1])
            except ValueError:
                continue
            if pid in target_pids:
                result[pid] = row[0]
        return result

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

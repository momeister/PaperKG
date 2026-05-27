from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import yaml


DEFAULT_KIMI_NIM_IMAGE = "nvcr.io/nim/moonshotai/kimi-k2.6:1.7.0-variant"


@dataclass(frozen=True)
class NIMContainerConfig:
    docker_executable: str = "docker"
    container_name: str = "sciencekg-kimi-k2-6-nim"
    image: str = DEFAULT_KIMI_NIM_IMAGE
    host: str = "localhost"
    host_port: int = 8000
    container_port: int = 8000
    cache_dir: str = "~/.cache/nim"
    shm_size: str = "32GB"
    gpus: str = "all"
    runtime: str = "nvidia"
    env_key_name: str = "NGC_API_KEY"
    extra_args: list[str] = field(default_factory=list)

    @classmethod
    def from_config_file(cls, config_path: str | Path = "config.yaml") -> "NIMContainerConfig":
        path = Path(config_path)
        _load_dotenv(path.parent / ".env")
        with path.open("r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}

        llm_cfg = config.get("llm") or {}
        raw = dict(llm_cfg.get("nim_container") or {})
        providers = llm_cfg.get("providers") or {}
        local_provider = providers.get("nvidia_local_nim") or {}
        inferred_port = _port_from_url(str(local_provider.get("base_url", "")))

        return cls(
            docker_executable=str(raw.get("docker_executable", cls.docker_executable)),
            container_name=str(raw.get("container_name", cls.container_name)),
            image=str(raw.get("image", cls.image)),
            host=str(raw.get("host", cls.host)),
            host_port=int(raw.get("host_port", inferred_port or cls.host_port)),
            container_port=int(raw.get("container_port", cls.container_port)),
            cache_dir=str(raw.get("cache_dir", cls.cache_dir)),
            shm_size=str(raw.get("shm_size", cls.shm_size)),
            gpus=str(raw.get("gpus", cls.gpus)),
            runtime=str(raw.get("runtime", cls.runtime)),
            env_key_name=str(raw.get("env_key_name", cls.env_key_name)),
            extra_args=[str(arg) for arg in (raw.get("extra_args") or [])],
        )

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.host_port}/v1"

    @property
    def api_key(self) -> str | None:
        return os.getenv(self.env_key_name) or os.getenv("NGC_API_KEY") or os.getenv("NVIDIA_API_KEY")

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def resolved_cache_dir(self) -> Path:
        return Path(os.path.expandvars(os.path.expanduser(self.cache_dir))).resolve()

    def with_overrides(self, **kwargs: Any) -> "NIMContainerConfig":
        clean = {key: value for key, value in kwargs.items() if value is not None}
        return replace(self, **clean)


@dataclass(frozen=True)
class NIMCommandResult:
    ok: bool
    returncode: int
    command: list[str]
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        return "\n".join(part for part in [self.stdout.strip(), self.stderr.strip()] if part)


@dataclass(frozen=True)
class NIMContainerStatus:
    docker_available: bool
    daemon_available: bool
    container_exists: bool
    running: bool
    status: str = "unknown"
    image: str = ""
    container_id: str = ""
    error: str = ""


Runner = Callable[..., subprocess.CompletedProcess[str]]


class NIMContainerManager:
    """Small Docker wrapper for a local NVIDIA NIM container."""

    def __init__(self, config: NIMContainerConfig, runner: Runner | None = None) -> None:
        self.config = config
        self._runner = runner or subprocess.run

    def redacted_run_command(self) -> list[str]:
        return self._run_args()

    def status(self) -> NIMContainerStatus:
        version = self._run([self.config.docker_executable, "--version"], timeout=15)
        if not version.ok:
            return NIMContainerStatus(
                docker_available=False,
                daemon_available=False,
                container_exists=False,
                running=False,
                status="docker_missing",
                error=version.output or "Docker CLI not found.",
            )

        info = self._run([self.config.docker_executable, "info", "--format", "{{.ServerVersion}}"], timeout=15)
        if not info.ok:
            return NIMContainerStatus(
                docker_available=True,
                daemon_available=False,
                container_exists=False,
                running=False,
                status="daemon_unavailable",
                error=info.output or "Docker daemon is not reachable.",
            )

        inspect = self._run([self.config.docker_executable, "inspect", self.config.container_name], timeout=15)
        if not inspect.ok:
            return NIMContainerStatus(
                docker_available=True,
                daemon_available=True,
                container_exists=False,
                running=False,
                status="not_created",
                error=inspect.output,
            )

        try:
            payload = json.loads(inspect.stdout)[0]
            state = payload.get("State") or {}
            config = payload.get("Config") or {}
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            return NIMContainerStatus(
                docker_available=True,
                daemon_available=True,
                container_exists=True,
                running=False,
                status="inspect_parse_failed",
                error=str(exc),
            )

        return NIMContainerStatus(
            docker_available=True,
            daemon_available=True,
            container_exists=True,
            running=bool(state.get("Running")),
            status=str(state.get("Status") or "unknown"),
            image=str(config.get("Image") or payload.get("Image") or ""),
            container_id=str(payload.get("Id") or "")[:12],
            error=str(state.get("Error") or ""),
        )

    def login_registry(self) -> NIMCommandResult:
        api_key = self.config.api_key
        if not api_key:
            return NIMCommandResult(
                ok=False,
                returncode=1,
                command=[self.config.docker_executable, "login", "nvcr.io", "--username", "$oauthtoken", "--password-stdin"],
                stderr="NGC_API_KEY or NVIDIA_API_KEY is not set.",
            )
        return self._run(
            [self.config.docker_executable, "login", "nvcr.io", "--username", "$oauthtoken", "--password-stdin"],
            input_text=api_key,
            timeout=120,
        )

    def pull_image(self) -> NIMCommandResult:
        return self._run([self.config.docker_executable, "pull", self.config.image], timeout=3600)

    def start_container(self) -> NIMCommandResult:
        status = self.status()
        if status.running:
            return NIMCommandResult(
                ok=True,
                returncode=0,
                command=[self.config.docker_executable, "start", self.config.container_name],
                stdout="Container is already running.",
            )
        if status.container_exists:
            return self._run([self.config.docker_executable, "start", self.config.container_name], timeout=120)
        if not self.config.has_api_key:
            return NIMCommandResult(
                ok=False,
                returncode=1,
                command=self.redacted_run_command(),
                stderr="NGC_API_KEY or NVIDIA_API_KEY is not set.",
            )

        cache_dir = self.config.resolved_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env[self.config.env_key_name] = self.config.api_key or ""
        return self._run(self._run_args(), env=env, timeout=3600)

    def stop_container(self) -> NIMCommandResult:
        return self._run([self.config.docker_executable, "stop", self.config.container_name], timeout=300)

    def logs(self, tail: int = 80) -> NIMCommandResult:
        return self._run(
            [self.config.docker_executable, "logs", "--tail", str(max(1, min(tail, 500))), self.config.container_name],
            timeout=30,
        )

    def _run_args(self) -> list[str]:
        args = [
            self.config.docker_executable,
            "run",
            "-d",
            "--name",
            self.config.container_name,
            "--runtime",
            self.config.runtime,
            "--gpus",
            self.config.gpus,
            "--shm-size",
            self.config.shm_size,
            "-e",
            self.config.env_key_name,
            "-v",
            f"{self.config.resolved_cache_dir()}:/opt/nim/.cache",
            "-p",
            f"{self.config.host_port}:{self.config.container_port}",
        ]
        args.extend(self.config.extra_args)
        args.append(self.config.image)
        return args

    def _run(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 60,
    ) -> NIMCommandResult:
        try:
            completed = self._runner(
                args,
                input=input_text,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            return NIMCommandResult(ok=False, returncode=127, command=args, stderr=str(exc))
        except subprocess.TimeoutExpired as exc:
            return NIMCommandResult(ok=False, returncode=124, command=args, stdout=exc.stdout or "", stderr=exc.stderr or "Command timed out.")
        return NIMCommandResult(
            ok=completed.returncode == 0,
            returncode=completed.returncode,
            command=args,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv(dotenv_path=env_path, override=False)


def _port_from_url(value: str) -> int | None:
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    return parsed.port

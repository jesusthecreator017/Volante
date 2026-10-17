"""All settings come from the environment (production: /etc/oso-arena/arena.env, loaded by systemd). Every OWNER DECISION from
PROMPT.md section 12 that has a number is a value here, with the neutral default."""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name, default):
    return os.environ.get("OSO_ARENA_" + name, default)


def _int(name, default):
    return int(_env(name, default))


def _bool(name, default):
    return str(_env(name, "1" if default else "0")).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "/var/lib/oso-arena")))
    bundle_dir: Path = field(default_factory=lambda: Path(_env("BUNDLE_DIR", "/opt/oso-arena/bundle")))
    base_url: str = field(default_factory=lambda: _env("BASE_URL", "http://127.0.0.1:8700").rstrip("/"))
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("PORT", 8700))
    health_port: int = field(default_factory=lambda: _int("HEALTH_PORT", 8701))
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", True))
    hsts: bool = field(default_factory=lambda: _bool("HSTS", True))
    session_secret: str = field(default_factory=lambda: _env("SESSION_SECRET", ""))
    github_client_id: str = field(default_factory=lambda: _env("GITHUB_CLIENT_ID", ""))
    github_client_secret: str = field(default_factory=lambda: _env("GITHUB_CLIENT_SECRET", ""))
    # client IP: only taken from this header, only when the TCP peer is loopback (the local Tailscale Funnel proxy); "" = never
    client_ip_header: str = field(default_factory=lambda: _env("CLIENT_IP_HEADER", "X-Forwarded-For"))
    # D5 rate limits (per client IP per minute; writes also per user)
    rate_reads: int = field(default_factory=lambda: _int("RATE_READS_PER_MIN", 120))
    rate_writes: int = field(default_factory=lambda: _int("RATE_WRITES_PER_MIN", 20))
    rate_replay: int = field(default_factory=lambda: _int("RATE_REPLAY_PER_MIN", 600))
    rate_global: int = field(default_factory=lambda: _int("RATE_GLOBAL_PER_MIN", 6000))
    daily_uploads: int = field(default_factory=lambda: _int("DAILY_UPLOADS", 5))
    max_upload: int = 1_048_576
    # ladder
    placement_games: int = field(default_factory=lambda: _int("PLACEMENT_GAMES", 10))
    provisional_games: int = field(default_factory=lambda: _int("PROVISIONAL_GAMES", 20))
    tournament_min_games: int = field(default_factory=lambda: _int("TOURNAMENT_MIN_GAMES", 20))
    pair_range: float = field(default_factory=lambda: float(_env("PAIR_RANGE", "200")))
    match_cooldown_s: int = field(default_factory=lambda: _int("MATCH_COOLDOWN_S", 300))  # a model plays at most once per this
    match_queue_max: int = field(default_factory=lambda: _int("MATCH_QUEUE_MAX", 6))
    # D6 tournament: weekday 0 = Monday .. 6 = Sunday, local time of TZ (the "server time" the site shows)
    tournament_weekday: int = field(default_factory=lambda: _int("TOURNAMENT_WEEKDAY", 6))
    tournament_hour: int = field(default_factory=lambda: _int("TOURNAMENT_HOUR", 18))
    tournament_size: int = 8
    # replays
    replay_cache_bytes: int = field(default_factory=lambda: _int("REPLAY_CACHE_BYTES", 5_000_000_000))
    replay_queue_max: int = field(default_factory=lambda: _int("REPLAY_QUEUE_MAX", 50))
    replay_keep_latest: int = 20
    # sandbox: "systemd" (production: oso-arena-child@<job>.service as oso-arena-sandbox) or "subprocess" (development/tests)
    sandbox: str = field(default_factory=lambda: _env("SANDBOX", "systemd"))
    python: str = field(default_factory=lambda: _env("PYTHON", "/opt/oso-arena/venv/bin/python"))
    base_model: str = field(default_factory=lambda: _env("BASE_MODEL", ""))  # path to base_40min.safetensors when it arrives
    backup_dir: Path = field(default_factory=lambda: Path(_env("BACKUP_DIR", "/var/backups/oso-arena")))  # D3
    min_free_bytes: int = field(default_factory=lambda: _int("MIN_FREE_BYTES", 2_000_000_000))

    @property
    def db_path(self):
        return self.data_dir / "db" / "arena.sqlite3"

    @property
    def models_dir(self):
        return self.data_dir / "models"

    @property
    def quarantine_dir(self):
        return self.data_dir / "quarantine"

    @property
    def logs_dir(self):
        return self.data_dir / "logs"

    @property
    def replays_dir(self):
        return self.data_dir / "replays"

    @property
    def jobs_dir(self):
        return self.data_dir / "jobs"

    @property
    def site_dir(self):
        return self.bundle_dir / "site"

    @property
    def viewer_dir(self):
        return self.bundle_dir / "viewer"


# per job kind: (RLIMIT_CPU seconds, wall-clock seconds). Measured on this Pi 5: a check 1-36 s, a match ~3 s, a replay 6-11 s;
# the PROMPT.md minimums are far above that.
LIMITS = {"check": (300, 600), "match": (900, 1800), "replay": (600, 1200)}
RLIMIT_AS = 2 << 30
RLIMIT_FSIZE = 256 << 20
RLIMIT_NOFILE = 64

_cfg = None


def get():
    global _cfg
    if _cfg is None:
        _cfg = Config()
    return _cfg


def reset():
    global _cfg
    _cfg = None

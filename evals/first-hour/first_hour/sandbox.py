from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from first_hour import policy

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

TOOLCHAIN = ("claude", "uv", "uvx", "just")
CREDENTIALS = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_SESSION_ACCESS_TOKEN",
)
KEEP = ("LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "USER", "LOGNAME")
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"
INSTALLED_AT = "2026-01-01T00:00:00.000Z"
GITCONFIG = """\
[user]
\tname = First-hour owner
\temail = owner@first-hour.invalid
[init]
\tdefaultBranch = main
[credential]
\thelper =
[url "file:///nonexistent/first-hour-never-pushes/"]
\tpushInsteadOf = https://
\tpushInsteadOf = http://
\tpushInsteadOf = ssh://
\tpushInsteadOf = git://
\tpushInsteadOf = git@
"""


@dataclass(frozen=True)
class Layout:
    root: Path
    toolchains: tuple[Path, ...] = ()

    @property
    def app(self) -> Path:
        return self.root / "app"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def config(self) -> Path:
        return self.home / ".claude"

    @property
    def plugins(self) -> Path:
        return self.config / "plugins"

    @property
    def gh(self) -> Path:
        return self.home / ".config" / "gh"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def bin(self) -> Path:
        return self.root / "bin"

    @property
    def pinned(self) -> Path:
        return self.root / "plugin"

    @property
    def owner(self) -> Path:
        return self.root / "owner"

    @property
    def appcheck(self) -> Path:
        return self.root / "appcheck"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def runtime(self) -> Path:
        digest = hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        return Path(tempfile.gettempdir()) / f"first-hour-{digest}"

    @property
    def shell(self) -> Path:
        return self.bin / "claude-shell"

    @property
    def writable(self) -> tuple[Path, ...]:
        return (self.app, self.home, self.tmp)

    def scope(self) -> policy.Scope:
        return policy.Scope(
            root=self.root,
            cwd=self.app,
            home=self.home,
            tmp=self.tmp,
            writable=self.writable,
            readonly=(self.plugins,),
        )


def environment(layout: Layout, inherited: Mapping[str, str]) -> dict[str, str]:
    env = {k: v for k in KEEP if (v := inherited.get(k))}
    gems = layout.home / ".gem"
    env.update(
        {
            "PATH": ":".join(
                [
                    str(layout.bin),
                    *map(str, map(_on_path, layout.toolchains)),
                    str(gems / "bin"),
                    SYSTEM_PATH,
                ]
            ),
            "HOME": str(layout.home),
            "SHELL": "/bin/sh",
            "TMPDIR": str(layout.tmp),
            "XDG_CONFIG_HOME": str(layout.home / ".config"),
            "XDG_CACHE_HOME": str(layout.home / ".cache"),
            "XDG_DATA_HOME": str(layout.home / ".local" / "share"),
            "XDG_STATE_HOME": str(layout.home / ".local" / "state"),
            "XDG_RUNTIME_DIR": str(layout.runtime),
            "RATCHET_PLUGIN": str(layout.pinned),
            "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
            "GEM_HOME": str(gems),
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_WEB_FETCH": "1",
            **session_env(layout),
        }
    )
    return env


def session_env(layout: Layout) -> dict[str, str]:
    return {
        "CLAUDE_CODE_SHELL_PREFIX": str(layout.shell),
        "GH_CONFIG_DIR": str(layout.gh),
        "GH_TOKEN": "",
        "GITHUB_TOKEN": "",
        "GH_ENTERPRISE_TOKEN": "",
        "GLAB_CONFIG_DIR": str(layout.home / ".config" / "glab-cli"),
        "GITLAB_TOKEN": "",
        "GIT_CONFIG_GLOBAL": str(layout.home / ".gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_MERGE_AUTOEDIT": "no",
        "SSH_AUTH_SOCK": "",
    }


def _on_path(tree: Path) -> Path:
    return tree / "bin" if (tree / "bin").is_dir() else tree


def toolchain(found: Mapping[str, str | None]) -> dict[str, Path]:
    return {name: Path(path).resolve() for name, path in found.items() if path}


def argv(
    layout: Layout,
    command: Sequence[str],
    *,
    tools: Iterable[Path] = (),
    writable: Sequence[Path] | None = None,
) -> list[str]:
    trees = layout.writable if writable is None else tuple(writable)
    args = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    args += ["--ro-bind", str(_passwd(layout)), "/etc/passwd"]
    args += ["--tmpfs", "/home", "--tmpfs", "/run/user"]
    args += ["--bind", str(layout.tmp), "/tmp", "--bind", str(layout.tmp), "/var/tmp"]
    args += ["--ro-bind", str(layout.root), str(layout.root)]
    for tree in trees:
        args += ["--bind", str(tree), str(tree)]
    args += ["--ro-bind", str(layout.plugins), str(layout.plugins)]
    for tool in sorted({*tools, *layout.toolchains}):
        args += ["--ro-bind", str(tool), str(tool)]
    args += ["--remount-ro", "/home"]
    args += ["--unsetenv", "XDG_RUNTIME_DIR"]
    args += ["--unshare-pid", "--unshare-ipc", "--die-with-parent", "--new-session"]
    return [*args, "--", *command]


def _passwd(layout: Layout) -> Path:
    return layout.root / "passwd"


def _with_home(passwd: str, uid: int, home: Path) -> str:
    entries = [line.split(":") for line in passwd.splitlines()]
    for entry in entries:
        if entry[2:3] == [str(uid)] and entry[5:6]:
            entry[5] = str(home)
    return "".join(":".join(entry) + "\n" for entry in entries)


def shell_prefix(layout: Layout, tools: Mapping[str, Path]) -> str:
    unset = [w for name in CREDENTIALS for w in ("-u", name)]
    words = ["env", *unset, *argv(layout, ["/bin/bash", "-c"], tools=tools.values())]
    quoted = " ".join(_quote(w) for w in words)
    return f'#!/bin/sh\nexec {quoted} "$1"\n'


def _quote(word: str) -> str:
    return "'" + word.replace("'", "'\"'\"'") + "'"


@dataclass(frozen=True)
class Plugin:
    key: str
    version: str
    commit: str | None
    path: Path
    source: Mapping[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.key.split("@", 1)[0]

    @property
    def marketplace(self) -> str:
        return self.key.split("@", 1)[1]


def installed(
    registry: Mapping[str, object], markets: Mapping[str, object], names: Iterable[str]
) -> list[Plugin]:
    plugins = registry.get("plugins")
    wanted = set(names)
    found = []
    for key, entries in (plugins if isinstance(plugins, dict) else {}).items():
        if key.split("@", 1)[0] not in wanted or not isinstance(entries, list) or not entries:
            continue
        entry = entries[0]
        market = markets.get(key.split("@", 1)[-1])
        source = market.get("source") if isinstance(market, dict) else None
        found.append(
            Plugin(
                key=key,
                version=str(entry.get("version")),
                commit=entry.get("gitCommitSha"),
                path=Path(str(entry.get("installPath"))),
                source=source if isinstance(source, dict) else {},
            )
        )
    return sorted(found, key=lambda p: p.key)


def copied(layout: Layout, plugin: Plugin) -> Path:
    if layout.root in plugin.path.parents:
        return plugin.path
    return layout.plugins / "cache" / plugin.marketplace / plugin.name / plugin.version


def prepare(layout: Layout, tools: Mapping[str, Path], plugins: Sequence[Plugin]) -> None:
    for path in (
        layout.app,
        layout.tmp,
        layout.bin,
        layout.gh,
        layout.plugins,
        layout.owner,
        layout.logs,
    ):
        path.mkdir(parents=True, exist_ok=True)
    layout.appcheck.mkdir(exist_ok=True)
    layout.runtime.mkdir(mode=0o700, exist_ok=True)
    (layout.home / ".gitconfig").write_text(GITCONFIG, encoding="utf-8")
    system = Path("/etc/passwd").read_text(encoding="utf-8")
    _passwd(layout).write_text(_with_home(system, os.getuid(), layout.home), encoding="utf-8")
    layout.shell.write_text(shell_prefix(layout, tools), encoding="utf-8")
    layout.shell.chmod(0o755)
    for name, path in tools.items():
        link = layout.bin / name
        if not link.is_symlink():
            link.symlink_to(path)
    entries: dict[str, object] = {}
    markets: dict[str, object] = {}
    for plugin in plugins:
        target = copied(layout, plugin)
        if not target.exists():
            shutil.copytree(plugin.path, target, symlinks=True)
        entry: dict[str, object] = {
            "scope": "user",
            "installPath": str(target),
            "version": plugin.version,
            "installedAt": INSTALLED_AT,
            "lastUpdated": INSTALLED_AT,
        }
        if plugin.commit:
            entry["gitCommitSha"] = plugin.commit
        entries[plugin.key] = [entry]
        markets[plugin.marketplace] = _marketplace(layout, plugin)
    registry = {"version": 2, "plugins": entries}
    (layout.plugins / "installed_plugins.json").write_text(json.dumps(registry, indent=2) + "\n")
    (layout.plugins / "known_marketplaces.json").write_text(json.dumps(markets, indent=2) + "\n")
    settings = {"enabledPlugins": dict.fromkeys(entries, True)}
    (layout.config / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")


def _marketplace(layout: Layout, plugin: Plugin) -> dict[str, object]:
    where = layout.plugins / "marketplaces" / plugin.marketplace
    manifest = {
        "name": plugin.marketplace,
        "owner": {"name": plugin.marketplace},
        "plugins": [{"name": plugin.name, "source": "./", "version": plugin.version}],
    }
    (where / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (where / ".claude-plugin" / "marketplace.json").write_text(json.dumps(manifest, indent=2))
    return {
        "source": dict(plugin.source),
        "installLocation": str(where),
        "lastUpdated": INSTALLED_AT,
    }


def settings(layout: Layout) -> dict[str, object]:
    return {
        "env": session_env(layout),
        "disableClaudeAiConnectors": True,
        "enableAllProjectMcpServers": False,
        "autoMemoryEnabled": False,
        "permissions": {"deny": list(policy.DENIED_TOOLS)},
    }


MARGIN_MIN = 10.0


def model_token(credentials: Mapping[str, object], *, now: float, minutes: float) -> str:
    oauth = credentials.get("claudeAiOauth")
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    expires = oauth.get("expiresAt") if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not token or not isinstance(expires, (int, float)):
        msg = "no Claude sign-in found: sign in with `claude` first (a subscription login)"
        raise SystemExit(msg)
    left = (expires / 1000 - now) / 60
    if left < minutes + MARGIN_MIN:
        msg = (
            f"the Claude sign-in expires in {max(left, 0):.0f} minutes, before a run of"
            f" {minutes:g} minutes ends: start any Claude Code session to renew it, or lower"
            " --minutes"
        )
        raise SystemExit(msg)
    return token

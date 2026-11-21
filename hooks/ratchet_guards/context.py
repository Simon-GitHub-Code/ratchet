import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from ratchet_guards.environment import TRACKED
from ratchet_guards.paths import Hit, Locator
from ratchet_guards.refs import MAIN_BRANCHES
from ratchet_guards.verdict import ACTING_RULES, UNADOPTED, Verdict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import justfile_text

DETACHED = "HEAD"
RECIPES = "justfile"
MERGES_BY_ITSELF = ("merge-ready", "merge")


class RepoState(Protocol):
    def current_branch(self, cwd: Path) -> str | None: ...

    def checked_out(self, root: Path) -> str | None: ...

    def default_branches(self, root: Path) -> frozenset[str]: ...

    def head_is_pushed(self, cwd: Path) -> bool | None: ...

    def awaiting_final(self, cwd: Path) -> str | None: ...

    def default_file(self, root: Path, relative: str) -> str | None: ...

    def full_name(self, cwd: Path, name: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class Context:
    cwd: Path
    branch: str | None = None
    maybe: frozenset[str] = frozenset()
    exported: frozenset[str] = frozenset()
    unread: bool = False
    unsettled: bool = False
    lost: bool = False
    unspelled: bool = False
    hidden_switch: bool = False

    def moved_to(self, cwd: Path, *, anchored: bool = True) -> "Context":
        return replace(
            self,
            cwd=cwd,
            branch=None,
            maybe=self._claims(),
            lost=self.lost and not anchored,
            unspelled=self.unspelled and not anchored,
        )

    def adrift(self) -> "Context":
        return replace(self, lost=True)

    def unspelling(self) -> "Context":
        return replace(self, lost=True, unspelled=True)

    def switched_to(self, branch: str) -> "Context":
        return replace(self, branch=branch, maybe=frozenset())

    def claiming(self, branches: Iterable[str]) -> "Context":
        return replace(self, maybe=self.maybe | frozenset(branches))

    def exporting(self, names: Iterable[str]) -> "Context":
        return replace(self, exported=self.exported | (TRACKED & frozenset(names)))

    def unsettling(self) -> "Context":
        return replace(self, unsettled=True)

    def switching_unseen(self) -> "Context":
        return replace(self, hidden_switch=True)

    def elsewhere(self) -> "Context":
        return replace(self, unread=True)

    def _claims(self) -> frozenset[str]:
        return self.maybe | (frozenset({self.branch}) if self.branch is not None else frozenset())


@dataclass(frozen=True, slots=True)
class Guard:
    locator: Locator
    repo: RepoState
    found: list[Verdict] = field(default_factory=list, compare=False)

    def noted(self, verdicts: list[Verdict]) -> list[Verdict]:
        self.found.extend(verdicts)
        return verdicts

    def branches(self, context: Context, cwd: Path) -> frozenset[str]:
        if context.unread:
            return context.maybe | MAIN_BRANCHES
        certain = context.branch is not None and cwd == context.cwd
        here = context.branch if certain else self.repo.current_branch(cwd)
        return context.maybe | (frozenset({here}) if here else frozenset())

    def defaults(self, context: Context, cwd: Path) -> frozenset[str]:
        return MAIN_BRANCHES if context.unread else self.repo.default_branches(cwd)

    def on_main(self, context: Context, cwd: Path) -> bool:
        return bool(self.branches(context, cwd) & self.defaults(context, cwd))

    def lands_now(self, context: Context, path: Path) -> bool:
        filesystem = self.locator.filesystem
        root = filesystem.checkout(filesystem.canonical(path))
        if root is None or context.hidden_switch:
            return True
        names = self.repo.default_branches(root)
        own = root == filesystem.checkout(filesystem.canonical(context.cwd))
        head = context.branch if own and context.branch is not None else self.repo.checked_out(root)
        return head is None or bool((context.maybe | {head}) & names)

    def writes(
        self, context: Context, hits: Iterable[Hit], detail: Callable[[Hit], str]
    ) -> list[Verdict]:
        seen = replace(context, hidden_switch=False)
        verdicts: list[Verdict] = []
        for hit in hits:
            if hit.group.locked:
                rule = "guard-path-write"
            elif self.lands_now(seen, hit.at):
                rule = "guard-path-default"
            elif context.hidden_switch:
                rule = "guard-path-unseen-switch"
            else:
                continue
            verdicts.append(Verdict(rule, detail(hit), hit.group.source))
        return verdicts

    def merging(self, rule: str, detail: str, cwd: Path) -> Verdict:
        filesystem = self.locator.filesystem
        checkout = filesystem.guarded_checkout(filesystem.canonical(cwd))
        recipes = None if checkout is None else self.repo.default_file(checkout.root, RECIPES)
        refused = recipes is not None and all(
            justfile_text.defines_recipe(recipes, name) for name in MERGES_BY_ITSELF
        )
        return Verdict(rule if refused else UNADOPTED[rule], detail)

    def acting(self, paths: Iterable[Path]) -> list[Verdict]:
        found = (self.locator.acting(path) for path in paths)
        return [Verdict(ACTING_RULES[file.kind], file.path) for file in found if file is not None]

    def unfollowed(
        self, context: Context, words: Iterable[str], *, destructive: bool, by: str
    ) -> list[Verdict]:
        if not context.lost:
            return []
        if context.unspelled:
            words = self.locator.relative(words)
        else:
            words = self.locator.could_hit(words, destructive=destructive)
        verb = "removed" if destructive else "changed"
        return [Verdict("unresolved-directory", f"{word}, {verb} by {by}") for word in words]

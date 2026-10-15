from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from functools import wraps
from typing import Any

import yaml

from rh_support_lib.models import (
    CaseCheckpoint,
    CaseLatestKnown,
    FollowedCase,
    TrackedCase,
)


def _locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._transaction():
            return method(self, *args, **kwargs)

    return locked


class StateService:
    """Service to manage persistent local operational state (followed cases, sync checkpoints,

    pinned cases, tags, etc.) in state.yaml with atomic file updates.
    """

    def __init__(
        self,
        state_file: str | None = None,
        config_file: str | None = None,
    ):
        self.state_file = self._resolve_state_path(state_file, config_file)
        self._lock = threading.RLock()
        self._lock_file = None

    @contextlib.contextmanager
    def _transaction(self):
        with self._lock:
            if self._lock_file is not None:
                yield
                return
            os.makedirs(
                os.path.dirname(os.path.abspath(self.state_file)), exist_ok=True
            )
            # Lock a stable sidecar inode, not the state inode replaced on every save.
            # Keep it on disk: unlinking it would let waiters lock different inodes.
            with open(self.state_file + ".lock", "a") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                self._lock_file = lock_file
                try:
                    yield
                finally:
                    self._lock_file = None
                    fcntl.flock(lock_file, fcntl.LOCK_UN)

    @staticmethod
    def _resolve_state_path(
        state_file: str | None = None,
        config_file: str | None = None,
    ) -> str:
        if state_file:
            return os.path.expanduser(state_file)
        if os.environ.get("RH_SUPPORT_STATE"):
            return os.path.expanduser(os.environ["RH_SUPPORT_STATE"])

        if config_file:
            return os.path.join(
                os.path.dirname(os.path.abspath(os.path.expanduser(config_file))),
                "state.yaml",
            )
        if os.environ.get("RH_SUPPORT_CONFIG"):
            return os.path.join(
                os.path.dirname(
                    os.path.abspath(os.path.expanduser(os.environ["RH_SUPPORT_CONFIG"]))
                ),
                "state.yaml",
            )

        # XDG State Specification: $XDG_STATE_HOME/rh-support-cli/state.yaml (default: ~/.local/state/rh-support-cli/state.yaml)
        xdg_state = os.environ.get("XDG_STATE_HOME")
        target_state_path = (
            os.path.join(os.path.expanduser(xdg_state), "rh-support-cli", "state.yaml")
            if xdg_state
            else os.path.expanduser("~/.local/state/rh-support-cli/state.yaml")
        )

        # Backward compatibility: migrate legacy state file from config directory if present
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        legacy_config_path = (
            os.path.join(os.path.expanduser(xdg_config), "rh-support-cli", "state.yaml")
            if xdg_config
            else os.path.expanduser("~/.config/rh-support-cli/state.yaml")
        )
        if not os.path.exists(target_state_path) and os.path.exists(legacy_config_path):
            migration = StateService(state_file=target_state_path)
            try:
                with migration._transaction():
                    # Another process may have migrated or updated state while we waited.
                    if os.path.exists(target_state_path):
                        return target_state_path
                    with contextlib.suppress(Exception):
                        shutil.copy2(legacy_config_path, target_state_path)
                        with contextlib.suppress(Exception):
                            os.remove(legacy_config_path)
                    if os.path.exists(target_state_path):
                        return target_state_path
            except OSError:
                # Migration is best-effort; never write without acquiring its lock.
                pass
            return legacy_config_path

        return target_state_path

    def load_state(self) -> dict[str, Any]:
        """Loads state data from the YAML state file."""
        with self._lock:
            if not os.path.isfile(self.state_file):
                return {"version": 1, "users": {}}
            try:
                with open(self.state_file, "r") as f:
                    data = yaml.safe_load(f) or {}
                    if not isinstance(data, dict):
                        data = {"version": 1, "users": {}}
                    data.setdefault("version", 1)
                    data.setdefault("users", {})
                    return data
            except (OSError, yaml.YAMLError):
                return {"version": 1, "users": {}}

    @_locked
    def save_state(self, data: dict[str, Any]) -> None:
        """Atomically saves state data to state.yaml."""
        with self._lock:
            dir_name = os.path.dirname(os.path.abspath(self.state_file))
            os.makedirs(dir_name, exist_ok=True)

            data.setdefault("version", 1)
            data.setdefault("users", {})

            # Write to temp file in same directory and atomic rename
            fd, temp_path = tempfile.mkstemp(
                dir=dir_name, prefix="state_", suffix=".tmp"
            )
            try:
                with open(fd, "w") as f:
                    yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
                os.replace(temp_path, self.state_file)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise

    def _get_user_state(self, data: dict[str, Any], user: str) -> dict[str, Any]:
        users = data.setdefault("users", {})
        return users.setdefault(user, {})

    # --- Tracked & Followed Cases ---

    @staticmethod
    def _parse_tracked_case(c_num: str, info: Any) -> TrackedCase:
        """Deserializes a tracked case record from dict or legacy string/list entry."""
        if isinstance(info, dict):
            cp_dict = info.get("read_checkpoint", {})
            lk_dict = info.get("latest_known", {})
            return TrackedCase(
                case_number=str(c_num),
                relationship=info.get("relationship", "followed"),
                followed_at=info.get("followed_at") or info.get("added_at", ""),
                summary=info.get("summary", ""),
                account_number=str(info.get("account_number", "")),
                source=info.get("source", "cli"),
                read_checkpoint=CaseCheckpoint(
                    status=cp_dict.get("status", ""),
                    severity=cp_dict.get("severity", ""),
                    last_read_comment_id=cp_dict.get("last_read_comment_id", ""),
                    last_read_comment_date=cp_dict.get("last_read_comment_date", ""),
                    marked_read_at=cp_dict.get("marked_read_at", ""),
                ),
                latest_known=CaseLatestKnown(
                    status=lk_dict.get("status", ""),
                    severity=lk_dict.get("severity", ""),
                    last_modified_date=lk_dict.get("last_modified_date", ""),
                    latest_comment_id=lk_dict.get("latest_comment_id", ""),
                    latest_comment_date=lk_dict.get("latest_comment_date", ""),
                    latest_comment_author=lk_dict.get("latest_comment_author", ""),
                    latest_comment_snippet=lk_dict.get("latest_comment_snippet", ""),
                    unread_comment_count=int(lk_dict.get("unread_comment_count", 0)),
                    status_changed=bool(lk_dict.get("status_changed", False)),
                ),
            )
        if isinstance(info, str):
            return TrackedCase(case_number=str(c_num), summary=info)
        return TrackedCase(case_number=str(c_num))

    def get_tracked_cases(self, user: str) -> dict[str, TrackedCase]:
        """Returns all tracked (owned, followed, or both) cases for a user."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        raw_tracked = user_state.get("tracked_cases")
        result: dict[str, TrackedCase] = {}

        if isinstance(raw_tracked, dict):
            for c_num, info in raw_tracked.items():
                result[str(c_num)] = self._parse_tracked_case(str(c_num), info)
        else:
            # Backwards compatibility: check legacy 'following'
            raw_following = user_state.get("following", {})
            if isinstance(raw_following, dict):
                for c_num, info in raw_following.items():
                    result[str(c_num)] = self._parse_tracked_case(str(c_num), info)
            elif isinstance(raw_following, list):
                for item in raw_following:
                    if isinstance(item, str):
                        result[item] = TrackedCase(case_number=item)
                    elif isinstance(item, dict) and "case_number" in item:
                        c_num = str(item["case_number"])
                        result[c_num] = self._parse_tracked_case(c_num, item)

        return result

    def get_followed_cases(self, user: str) -> dict[str, FollowedCase]:
        """Returns all followed cases for a user mapped by case number."""
        all_tracked = self.get_tracked_cases(user)
        return {
            c_num: tc
            for c_num, tc in all_tracked.items()
            if tc.relationship in ("followed", "both")
        }

    def get_owned_cases(self, user: str) -> dict[str, TrackedCase]:
        """Returns all owned cases tracked for a user."""
        all_tracked = self.get_tracked_cases(user)
        return {
            c_num: tc
            for c_num, tc in all_tracked.items()
            if tc.relationship in ("owned", "both")
        }

    def is_following(self, case_number: str, user: str) -> bool:
        """Checks if a case is currently followed by the user."""
        following = self.get_followed_cases(user)
        return str(case_number) in following

    @_locked
    def add_tracked_case(
        self,
        user: str,
        case_number: str,
        relationship: str = "followed",
        summary: str = "",
        account_number: str = "",
        status: str = "",
        severity: str = "",
        last_comment_id: str = "",
        last_comment_date: str = "",
        last_modified_date: str = "",
        source: str = "cli",
    ) -> None:
        """Adds or updates a tracked case record in state.yaml."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        tracked_dict = user_state.setdefault("tracked_cases", {})
        following_legacy = user_state.setdefault("following", {})
        c_num = str(case_number).strip()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        existing = tracked_dict.get(c_num)
        if not existing and isinstance(following_legacy.get(c_num), dict):
            existing = following_legacy.get(c_num)

        final_rel = relationship
        if existing and isinstance(existing, dict):
            cur_rel = existing.get("relationship", "followed")
            if (
                cur_rel != relationship
                and cur_rel in ("owned", "followed")
                or cur_rel == "both"
            ):
                final_rel = "both"

        existing_cp = (
            existing.get("read_checkpoint", {}) if isinstance(existing, dict) else {}
        )
        existing_lk = (
            existing.get("latest_known", {}) if isinstance(existing, dict) else {}
        )
        entry = {
            "relationship": final_rel,
            "followed_at": (
                existing.get("followed_at") or now_str
                if isinstance(existing, dict)
                else now_str
            ),
            "summary": summary
            or (existing.get("summary", "") if isinstance(existing, dict) else ""),
            "account_number": str(account_number)
            if account_number
            else (
                str(existing.get("account_number", ""))
                if isinstance(existing, dict)
                else ""
            ),
            "source": source
            or (existing.get("source", "cli") if isinstance(existing, dict) else "cli"),
            "read_checkpoint": existing_cp
            or {
                "status": status,
                "severity": severity,
                "last_read_comment_id": last_comment_id,
                "last_read_comment_date": last_comment_date,
                "marked_read_at": now_str,
            },
            "latest_known": existing_lk
            or {
                "status": status,
                "severity": severity,
                "last_modified_date": last_modified_date or now_str,
                "latest_comment_id": last_comment_id,
                "latest_comment_date": last_comment_date,
                "unread_comment_count": 0,
                "status_changed": False,
            },
        }
        tracked_dict[c_num] = entry
        if final_rel in ("followed", "both"):
            following_legacy[c_num] = {
                "added_at": entry["followed_at"],
                "source": entry["source"],
                "summary": entry["summary"],
                "account_number": entry["account_number"],
            }
        self.save_state(data)

    def add_followed_case(
        self,
        case_number: str,
        user: str,
        summary: str = "",
        account_number: str = "",
        source: str = "cli",
    ) -> None:
        """Backwards-compatibility alias for add_tracked_case with relationship='followed'."""
        self.add_tracked_case(
            user=user,
            case_number=case_number,
            relationship="followed",
            summary=summary,
            account_number=account_number,
            source=source,
        )

    def register_owned_case(
        self,
        user: str,
        case_number: str,
        summary: str = "",
        account_number: str = "",
        status: str = "",
        severity: str = "",
        last_comment_id: str = "",
        last_comment_date: str = "",
        last_modified_date: str = "",
    ) -> None:
        """Registers or updates an owned case in state.yaml."""
        self.add_tracked_case(
            user=user,
            case_number=case_number,
            relationship="owned",
            summary=summary,
            account_number=account_number,
            status=status,
            severity=severity,
            last_comment_id=last_comment_id,
            last_comment_date=last_comment_date,
            last_modified_date=last_modified_date,
            source="auto",
        )

    @_locked
    def remove_followed_case(self, case_number: str, user: str) -> bool:
        """Removes a case from following. If the case is also owned, retains it as owned."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        tracked_dict = user_state.get("tracked_cases", {})
        following_legacy = user_state.get("following", {})
        c_num = str(case_number).strip()
        modified = False

        if c_num in tracked_dict:
            cur_rel = tracked_dict[c_num].get("relationship", "followed")
            if cur_rel == "both":
                tracked_dict[c_num]["relationship"] = "owned"
                modified = True
            else:
                del tracked_dict[c_num]
                modified = True

        if isinstance(following_legacy, dict) and c_num in following_legacy:
            del following_legacy[c_num]
            modified = True
        elif isinstance(following_legacy, list):
            new_list = [
                i
                for i in following_legacy
                if (
                    i != c_num
                    and getattr(i, "get", lambda _: None)("case_number") != c_num
                )
            ]
            if len(new_list) != len(following_legacy):
                user_state["following"] = new_list
                modified = True

        if modified:
            self.save_state(data)
        return modified

    @_locked
    def toggle_follow(self, user: str, case_number: str) -> bool:
        """Toggles follow status for a case. Returns True if now following, False if unfollowed."""
        if self.is_following(case_number, user):
            self.remove_followed_case(case_number, user)
            return False
        self.add_followed_case(case_number, user)
        return True

    @_locked
    def batch_update_latest_known(
        self,
        user: str,
        case_updates: list[dict[str, Any]],
    ) -> None:
        """Atomically updates latest_known for multiple cases in a single thread-safe write."""
        if not case_updates:
            return
        with self._lock:
            data = self.load_state()
            user_state = self._get_user_state(data, user)
            tracked_dict = user_state.setdefault("tracked_cases", {})
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            for up in case_updates:
                c_num = str(up["case_number"]).strip()
                tc = tracked_dict.get(c_num)
                if not tc or not isinstance(tc, dict):
                    tc = {
                        "relationship": "followed",
                        "followed_at": now_str,
                        "summary": up.get("summary", ""),
                        "account_number": str(up.get("account_number", "")),
                        "source": "sync",
                        "read_checkpoint": {
                            "status": up.get("status", ""),
                            "severity": up.get("severity", ""),
                            "last_read_comment_id": up.get("latest_comment_id", ""),
                            "last_read_comment_date": up.get("latest_comment_date", ""),
                            "marked_read_at": now_str,
                        },
                        "latest_known": {},
                    }
                    tracked_dict[c_num] = tc

                cp = tc.setdefault("read_checkpoint", {})
                cp_status = cp.get("status", "")
                status = up.get("status", "")
                if not cp_status and status:
                    # Initialize previously empty checkpoint status so subsequent changes are tracked
                    cp["status"] = status
                    cp_status = status

                status_changed = up.get("status_changed")
                if status_changed is None:
                    status_changed = bool(cp_status and status and cp_status != status)

                tc["latest_known"] = {
                    "status": status,
                    "severity": up.get("severity", ""),
                    "last_modified_date": up.get("last_modified_date", ""),
                    "latest_comment_id": up.get("latest_comment_id", ""),
                    "latest_comment_date": up.get("latest_comment_date", ""),
                    "latest_comment_author": up.get("latest_comment_author", ""),
                    "latest_comment_snippet": up.get("latest_comment_snippet", ""),
                    "unread_comment_count": int(up.get("new_comment_count", 0)),
                    "status_changed": bool(status_changed),
                }
                if up.get("summary"):
                    tc["summary"] = up["summary"]
                if up.get("account_number"):
                    tc["account_number"] = str(up["account_number"])

            self.save_state(data)

    def update_latest_known(
        self,
        user: str,
        case_number: str,
        status: str,
        severity: str,
        last_modified_date: str,
        latest_comment_id: str = "",
        latest_comment_date: str = "",
        latest_comment_author: str = "",
        latest_comment_snippet: str = "",
        new_comment_count: int = 0,
        status_changed: bool | None = None,
        summary: str = "",
    ) -> None:
        """Updates the latest known remote state for a case, computing unread indicators."""
        self.batch_update_latest_known(
            user=user,
            case_updates=[
                {
                    "case_number": case_number,
                    "status": status,
                    "severity": severity,
                    "last_modified_date": last_modified_date,
                    "latest_comment_id": latest_comment_id,
                    "latest_comment_date": latest_comment_date,
                    "latest_comment_author": latest_comment_author,
                    "latest_comment_snippet": latest_comment_snippet,
                    "new_comment_count": new_comment_count,
                    "status_changed": status_changed,
                    "summary": summary,
                }
            ],
        )

    @_locked
    def mark_case_read(
        self,
        user: str,
        case_number: str,
        status: str | None = None,
        severity: str | None = None,
        last_comment_id: str | None = None,
        last_comment_date: str | None = None,
    ) -> None:
        """Advances read_checkpoint for a case, marking all updates as read."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        tracked_dict = user_state.setdefault("tracked_cases", {})
        c_num = str(case_number).strip()
        tc = tracked_dict.get(c_num)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not tc or not isinstance(tc, dict):
            tc = {
                "relationship": "viewed",
                "followed_at": "",
                "summary": "",
                "account_number": "",
                "source": "cli",
                "read_checkpoint": {},
                "latest_known": {},
            }
            tracked_dict[c_num] = tc
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lk = tc.get("latest_known", {})
        cp = tc.setdefault("read_checkpoint", {})

        cp["status"] = status or lk.get("status") or cp.get("status", "")
        cp["severity"] = severity or lk.get("severity") or cp.get("severity", "")
        cp["last_read_comment_id"] = (
            last_comment_id
            or lk.get("latest_comment_id")
            or cp.get("last_read_comment_id", "")
        )
        cp["last_read_comment_date"] = (
            last_comment_date or lk.get("latest_comment_date") or now_str
        )
        cp["marked_read_at"] = now_str

        if "latest_known" in tc:
            tc["latest_known"]["unread_comment_count"] = 0
            tc["latest_known"]["status_changed"] = False

        self.save_state(data)

    def get_unread_cases(self, user: str) -> list[TrackedCase]:
        """Returns all tracked cases that have unread comments or status changes."""
        all_tracked = self.get_tracked_cases(user)
        return [
            tc
            for tc in all_tracked.values()
            if tc.latest_known.unread_comment_count > 0
            or tc.latest_known.status_changed
        ]

    # --- Sync Checkpoints ---

    def get_sync_info(self, user: str, account_number: str) -> dict[str, Any]:
        """Returns sync checkpoint information for an account."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        sync_accounts = user_state.get("sync", {}).get("accounts", {})
        return sync_accounts.get(str(account_number), {})

    @_locked
    def update_sync_info(
        self,
        user: str,
        account_number: str,
        last_synced_at: str,
        scanned_count: int,
    ) -> None:
        """Updates sync checkpoint information for an account."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        sync_section = user_state.setdefault("sync", {})
        accounts = sync_section.setdefault("accounts", {})
        accounts[str(account_number)] = {
            "last_synced_at": last_synced_at,
            "last_scanned_case_count": scanned_count,
        }
        self.save_state(data)

    # --- Extensibility: Pinned, Recent, Tags ---

    def get_pinned_cases(self, user: str) -> list[str]:
        """Returns pinned case numbers for a user."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        return list(user_state.get("pinned", []))

    @_locked
    def set_pinned_cases(self, user: str, cases: list[str]) -> None:
        """Sets pinned case numbers for a user."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        user_state["pinned"] = [str(c) for c in cases]
        self.save_state(data)

    def get_recent_cases(self, user: str) -> list[dict[str, Any]]:
        """Returns recent case entries for a user."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        return list(user_state.get("recent", []))

    @_locked
    def add_recent_case(
        self, user: str, case_number: str, meta: dict[str, Any] | None = None
    ) -> None:
        """Records a recently viewed case (keeps top 50)."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        recent = user_state.setdefault("recent", [])

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        c_num = str(case_number)

        recent[:] = [
            r
            for r in recent
            if (r.get("case_number") != c_num if isinstance(r, dict) else r != c_num)
        ]
        entry = {"case_number": c_num, "viewed_at": now_str}
        if meta:
            entry.update(meta)
        recent.insert(0, entry)
        user_state["recent"] = recent[:50]
        self.save_state(data)

    def get_tags(self, user: str, case_number: str) -> list[str]:
        """Returns local tags for a specific case."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        tags_dict = user_state.get("tags", {})
        return list(tags_dict.get(str(case_number), []))

    @_locked
    def set_tags(self, user: str, case_number: str, tags: list[str]) -> None:
        """Sets local tags for a specific case."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        tags_dict = user_state.setdefault("tags", {})
        tags_dict[str(case_number)] = list(tags)
        self.save_state(data)

    def get_tui_layout(self, user: str) -> str:
        """Returns persistent TUI layout for user ('horizontal' or 'vertical')."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        return user_state.get("tui_layout", "horizontal")

    @_locked
    def set_tui_layout(self, user: str, layout: str) -> None:
        """Sets persistent TUI layout for user ('horizontal' or 'vertical')."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        user_state["tui_layout"] = (
            "vertical" if layout in ("vertical", "vsplit") else "horizontal"
        )
        self.save_state(data)

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from typing import Any

import yaml

from rh_support_lib.models import FollowedCase


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

        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return os.path.join(
                os.path.expanduser(xdg_config), "rh-support-cli", "state.yaml"
            )

        return os.path.expanduser("~/.config/rh-support-cli/state.yaml")

    def load_state(self) -> dict[str, Any]:
        """Loads state data from the YAML state file."""
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

    def save_state(self, data: dict[str, Any]) -> None:
        """Atomically saves state data to state.yaml."""
        dir_name = os.path.dirname(os.path.abspath(self.state_file))
        os.makedirs(dir_name, exist_ok=True)

        data.setdefault("version", 1)
        data.setdefault("users", {})

        # Write to temp file in same directory and atomic rename
        fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="state_", suffix=".tmp")
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

    # --- Followed Cases ---

    def get_followed_cases(self, user: str) -> dict[str, FollowedCase]:
        """Returns all followed cases for a user mapped by case number."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        raw_following = user_state.get("following", {})
        result: dict[str, FollowedCase] = {}

        if isinstance(raw_following, dict):
            for case_num, info in raw_following.items():
                if isinstance(info, dict):
                    result[str(case_num)] = FollowedCase(
                        case_number=str(case_num),
                        added_at=info.get("added_at", ""),
                        source=info.get("source", "cli"),
                        summary=info.get("summary", ""),
                        account_number=str(info.get("account_number", "")),
                    )
                elif isinstance(info, str):
                    result[str(case_num)] = FollowedCase(
                        case_number=str(case_num),
                        summary=info,
                    )
        elif isinstance(raw_following, list):
            for item in raw_following:
                if isinstance(item, str):
                    result[item] = FollowedCase(case_number=item)
                elif isinstance(item, dict) and "case_number" in item:
                    c_num = str(item["case_number"])
                    result[c_num] = FollowedCase(
                        case_number=c_num,
                        added_at=item.get("added_at", ""),
                        source=item.get("source", "cli"),
                        summary=item.get("summary", ""),
                        account_number=str(item.get("account_number", "")),
                    )

        return result

    def is_following(self, case_number: str, user: str) -> bool:
        """Checks if a case is currently followed by the user."""
        following = self.get_followed_cases(user)
        return str(case_number) in following

    def add_followed_case(
        self,
        case_number: str,
        user: str,
        summary: str = "",
        account_number: str = "",
        source: str = "cli",
    ) -> None:
        """Adds or updates a followed case in the user's state."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        following = user_state.setdefault("following", {})

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        c_num = str(case_number)

        existing = following.get(c_num)
        if isinstance(existing, dict):
            existing["summary"] = summary or existing.get("summary", "")
            existing["account_number"] = (
                str(account_number)
                if account_number
                else existing.get("account_number", "")
            )
            existing["source"] = source or existing.get("source", "cli")
        else:
            following[c_num] = {
                "added_at": now_str,
                "source": source,
                "summary": summary,
                "account_number": str(account_number) if account_number else "",
            }

        self.save_state(data)

    def remove_followed_case(self, case_number: str, user: str) -> bool:
        """Removes a followed case from the user's state. Returns True if removed."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        following = user_state.get("following", {})
        c_num = str(case_number)

        removed = False
        if isinstance(following, dict) and c_num in following:
            del following[c_num]
            removed = True
        elif isinstance(following, list):
            new_list = [
                item
                for item in following
                if (
                    item != c_num
                    and getattr(item, "get", lambda _: None)("case_number") != c_num
                )
            ]
            if len(new_list) != len(following):
                user_state["following"] = new_list
                removed = True

        if removed:
            self.save_state(data)
        return removed

    # --- Sync Checkpoints ---

    def get_sync_info(self, user: str, account_number: str) -> dict[str, Any]:
        """Returns sync checkpoint information for an account."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        sync_accounts = user_state.get("sync", {}).get("accounts", {})
        return sync_accounts.get(str(account_number), {})

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

    def set_tags(self, user: str, case_number: str, tags: list[str]) -> None:
        """Sets local tags for a specific case."""
        data = self.load_state()
        user_state = self._get_user_state(data, user)
        tags_dict = user_state.setdefault("tags", {})
        tags_dict[str(case_number)] = list(tags)
        self.save_state(data)

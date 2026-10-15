from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from typing import Any

from rh_support_lib.models import (
    Attachment,
    Case,
    CaseComment,
    CaseDetails,
    ProductItem,
)


class CacheService:
    """Manages local XDG disk caching for support cases, comments, case lists, and product catalog."""

    def __init__(
        self,
        cache_dir: str | None = None,
        config: dict | None = None,
        no_cache: bool = False,
        scope: str | None = None,
    ):
        self.config = config or {}
        cache_cfg = self.config.get("cache") or {}

        # Resolve enabled state
        cfg_enabled = (
            cache_cfg.get("enabled", True) if isinstance(cache_cfg, dict) else True
        )
        env_no_cache = os.environ.get("RH_NO_CACHE") in ("1", "true", "True")
        self.scope = scope if isinstance(scope, str) and scope else None
        self.enabled = (
            not no_cache
            and not env_no_cache
            and bool(cfg_enabled)
            and self.scope is not None
        )

        # Default TTLs (seconds)
        self.list_ttl = (
            int(cache_cfg.get("list_ttl", 60)) if isinstance(cache_cfg, dict) else 60
        )
        self.case_ttl = (
            int(cache_cfg.get("case_ttl", 60)) if isinstance(cache_cfg, dict) else 60
        )
        self.catalog_ttl = (
            int(cache_cfg.get("catalog_ttl", 86400))
            if isinstance(cache_cfg, dict)
            else 86400
        )
        # Resolve directory
        self.root_dir = self._resolve_cache_dir(cache_dir)
        namespace = (
            hashlib.sha256(self.scope.encode("utf-8")).hexdigest()
            if self.scope
            else "unavailable"
        )
        private_dir = os.path.join(self.root_dir, "private", namespace)
        self.cases_dir = os.path.join(private_dir, "cases")
        self.lists_dir = os.path.join(private_dir, "lists")
        self.catalog_dir = os.path.join(self.root_dir, "catalog")

        if self.enabled:
            os.makedirs(private_dir, mode=0o700, exist_ok=True)
            os.chmod(private_dir, 0o700)
            os.makedirs(self.cases_dir, exist_ok=True)
            os.makedirs(self.lists_dir, exist_ok=True)
            os.makedirs(self.catalog_dir, exist_ok=True)

    @staticmethod
    def _resolve_cache_dir(cache_dir: str | None = None) -> str:
        base = (
            cache_dir
            or os.environ.get("XDG_CACHE_HOME")
            or os.path.expanduser("~/.cache")
        )
        expanded = os.path.expanduser(base)
        if os.path.basename(expanded) == "rh-support-cli":
            return expanded
        return os.path.join(expanded, "rh-support-cli")

    # --- ATOMIC FILE I/O ---

    def _write_json_atomic(self, target_path: str, data: Any) -> None:
        """Atomically writes JSON payload to disk in a thread-safe manner."""
        if not self.enabled:
            return
        dir_name = os.path.dirname(os.path.abspath(target_path))
        os.makedirs(dir_name, exist_ok=True)

        prefix = f".tmp.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}."
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=dir_name,
                prefix=prefix,
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as f:
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
                tmp_name = f.name
            os.replace(tmp_name, target_path)
        except Exception:
            if tmp_name and os.path.exists(tmp_name):
                with contextlib.suppress(OSError):
                    os.remove(tmp_name)
            raise

    def _read_json(self, path: str) -> dict[str, Any] | list[Any] | None:
        if not self.enabled or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None

    # --- CASE DETAILS & COMMENTS CACHE (CONDITIONAL VALIDATION) ---

    def _case_path(self, case_number: str) -> str:
        case_number = str(case_number).strip()
        if (
            not case_number
            or case_number in (".", "..")
            or any(char in case_number for char in ("/", "\\"))
        ):
            raise ValueError("Invalid case identifier for the private cache.")
        return os.path.join(self.cases_dir, f"{case_number}.json")

    def get_case_record(self, case_number: str) -> dict[str, Any] | None:
        """Retrieves raw cached case record containing case, details, attachments, and last_modified_date."""
        if not self.enabled:
            return None
        path = self._case_path(case_number)
        data = self._read_json(path)
        return data if isinstance(data, dict) else None

    def get_all_cached_case_records(self) -> list[dict[str, Any]]:
        """Returns all cached raw case records from cases_dir."""
        if not self.enabled or not os.path.isdir(self.cases_dir):
            return []
        records: list[dict[str, Any]] = []
        for fname in os.listdir(self.cases_dir):
            if fname.endswith(".json"):
                path = os.path.join(self.cases_dir, fname)
                rec = self._read_json(path)
                if isinstance(rec, dict) and rec.get("case"):
                    records.append(rec)
        return records

    def get_case_details(
        self, case_number: str, ttl: int | None = None
    ) -> tuple[CaseDetails, list[Attachment], str] | None:
        """Loads and reconstructs CaseDetails, Attachments, and last_modified_date from cache."""
        record = self.get_case_record(case_number)
        if not record:
            return None

        max_age = ttl if ttl is not None else self.case_ttl
        cached_at = float(record.get("cached_at", 0))
        if max_age > 0 and (time.time() - cached_at) > max_age:
            return None
        try:
            case_dict = record.get("case") or {}
            case_obj = Case(
                case_number=str(case_dict.get("case_number", case_number)),
                summary=case_dict.get("summary", ""),
                status=case_dict.get("status", ""),
                severity=case_dict.get("severity", ""),
                product=case_dict.get("product", ""),
                version=case_dict.get("version", ""),
                account_number=case_dict.get("account_number", ""),
                created_date=case_dict.get("created_date", ""),
                last_modified_date=case_dict.get("last_modified_date", ""),
                created_by=case_dict.get("created_by", ""),
                last_modified_by=case_dict.get("last_modified_by", ""),
                owner=case_dict.get("owner", ""),
                contact_name=case_dict.get("contact_name", ""),
                contact_sso=case_dict.get("contact_sso", ""),
                case_type=case_dict.get("case_type", "Standard"),
                id=case_dict.get("id", ""),
                is_followed=case_dict.get("is_followed", False),
                has_unread=case_dict.get("has_unread", False),
                unread_count=case_dict.get("unread_count", 0),
                status_changed=case_dict.get("status_changed", False),
                old_status=case_dict.get("old_status", ""),
            )

            comments = []
            for cd in record.get("comments") or []:
                comments.append(
                    CaseComment(
                        id=cd.get("id", ""),
                        body=cd.get("body", ""),
                        created_date=cd.get("created_date", ""),
                        created_by=cd.get("created_by", ""),
                        is_public=cd.get("is_public", True),
                        visibility=cd.get("visibility", "Public"),
                        is_associate=cd.get("is_associate", False),
                        is_customer=cd.get("is_customer", False),
                    )
                )

            attachments = []
            for ad in record.get("attachments") or []:
                attachments.append(
                    Attachment(
                        uuid=ad.get("uuid", ""),
                        file_name=ad.get("file_name", ""),
                        size=int(ad.get("size", 0)),
                        size_kb=float(ad.get("size_kb", 0.0)),
                        created_date=ad.get("created_date", ""),
                        created_by=ad.get("created_by", ""),
                        description=ad.get("description", ""),
                        download_link=ad.get("download_link", ""),
                        upload_status=ad.get("upload_status", "Completed"),
                        case_number=str(ad.get("case_number", case_number)),
                    )
                )

            details = CaseDetails(
                case=case_obj,
                description=record.get("description", ""),
                comments=comments,
                attachments=attachments,
                notified_users=record.get("notified_users", []),
            )
            last_modified = record.get(
                "last_modified_date", case_obj.last_modified_date
            )
            return details, attachments, last_modified
        except Exception:  # noqa: BLE001
            return None

    def set_case_details(
        self,
        case_number: str,
        details: CaseDetails,
        attachments: list[Attachment] | None = None,
        last_modified_date: str | None = None,
    ) -> None:
        """Saves CaseDetails, comments, attachments, and last_modified timestamp to disk cache."""
        if not self.enabled:
            return
        c_num = str(case_number).strip()
        path = self._case_path(c_num)
        lmd = (
            last_modified_date
            or (details.case.last_modified_date if details.case else "")
            or ""
        )
        latest_c_id = details.comments[-1].id if details.comments else ""
        latest_c_date = details.comments[-1].created_date if details.comments else ""
        record = {
            "cached_at": time.time(),
            "last_modified_date": lmd,
            "status": details.case.status if details.case else "",
            "latest_comment_id": latest_c_id,
            "latest_comment_date": latest_c_date,
            "case": asdict(details.case) if details.case else {},
            "description": details.description,
            "comments": [asdict(c) for c in details.comments],
            "attachments": [asdict(a) for a in (attachments or details.attachments)],
            "notified_users": details.notified_users,
        }
        self._write_json_atomic(path, record)

    def invalidate_case(self, case_number: str) -> None:
        """Invalidates cached details for a specific case."""
        if not self.enabled:
            return
        path = self._case_path(case_number)
        if os.path.exists(path):
            with contextlib.suppress(OSError):
                os.remove(path)
        # Invalidate lists when a case mutates
        self.invalidate_case_lists()

    # --- CASE LIST CACHE (NORMALIZED DETERMINISTIC KEYS + TTL) ---

    @staticmethod
    def compute_list_cache_key(
        account: str | None = None,
        status: list[str] | str | None = None,
        severity: list[str] | str | None = None,
        owner: str | None = None,
        limit: int | None = None,
        all_pages: bool = False,
    ) -> str:
        """Computes a collision-free deterministic hash key based on query filters."""
        norm_status: list[str] = []
        if status:
            norm_status = (
                sorted([str(s).lower() for s in status])
                if isinstance(status, list)
                else [str(status).lower()]
            )

        norm_sev: list[str] = []
        if severity:
            norm_sev = (
                sorted([str(s).lower() for s in severity])
                if isinstance(severity, list)
                else [str(severity).lower()]
            )

        query_payload = {
            "account": str(account).strip() if account else None,
            "status": norm_status,
            "severity": norm_sev,
            "owner": str(owner).strip().lower() if owner else None,
            "limit": limit,
            "all_pages": all_pages,
        }
        serialized = json.dumps(query_payload, sort_keys=True)
        query_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
        return f"list_{query_hash}.json"

    def get_case_list(
        self, cache_key: str, ttl: int | None = None
    ) -> list[Case] | None:
        """Retrieves cached list of cases if within TTL."""
        if not self.enabled:
            return None
        path = os.path.join(self.lists_dir, cache_key)
        data = self._read_json(path)
        if not isinstance(data, dict):
            return None

        effective_ttl = ttl if ttl is not None else self.list_ttl
        cached_at = data.get("cached_at", 0)
        if (time.time() - cached_at) > effective_ttl:
            return None

        cases_raw = data.get("cases") or []
        cases = []
        try:
            for cd in cases_raw:
                cases.append(
                    Case(
                        case_number=str(cd.get("case_number", "")),
                        summary=cd.get("summary", ""),
                        status=cd.get("status", ""),
                        severity=cd.get("severity", ""),
                        product=cd.get("product", ""),
                        version=cd.get("version", ""),
                        account_number=cd.get("account_number", ""),
                        created_date=cd.get("created_date", ""),
                        last_modified_date=cd.get("last_modified_date", ""),
                        created_by=cd.get("created_by", ""),
                        last_modified_by=cd.get("last_modified_by", ""),
                        owner=cd.get("owner", ""),
                        contact_name=cd.get("contact_name", ""),
                        contact_sso=cd.get("contact_sso", ""),
                        case_type=cd.get("case_type", "Standard"),
                        id=cd.get("id", ""),
                        is_followed=cd.get("is_followed", False),
                        has_unread=cd.get("has_unread", False),
                        unread_count=cd.get("unread_count", 0),
                        status_changed=cd.get("status_changed", False),
                        old_status=cd.get("old_status", ""),
                    )
                )
            return cases
        except Exception:  # noqa: BLE001
            return None

    def set_case_list(self, cache_key: str, cases: list[Case]) -> None:
        """Saves a query case list to disk cache with current timestamp."""
        if not self.enabled:
            return
        path = os.path.join(self.lists_dir, cache_key)
        payload = {
            "cached_at": time.time(),
            "cases": [asdict(c) for c in cases],
        }
        self._write_json_atomic(path, payload)

    def invalidate_case_lists(self) -> None:
        """Invalidates all cached case lists."""
        if not self.enabled or not os.path.exists(self.lists_dir):
            return
        for f in os.listdir(self.lists_dir):
            if f.endswith(".json"):
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(self.lists_dir, f))

    # --- PRODUCT CATALOG CACHE ---

    def get_products(self, ttl: int | None = None) -> list[ProductItem] | None:
        """Retrieves cached product items if within catalog TTL."""
        if not self.enabled:
            return None
        path = os.path.join(self.catalog_dir, "products.json")
        data = self._read_json(path)
        if not isinstance(data, dict):
            return None

        effective_ttl = ttl if ttl is not None else self.catalog_ttl
        cached_at = data.get("cached_at", 0)
        if (time.time() - cached_at) > effective_ttl:
            return None

        products = []
        try:
            for item in data.get("products") or []:
                products.append(
                    ProductItem(
                        id=item.get("id", ""),
                        name=item.get("name", ""),
                        version_name=item.get("version_name", ""),
                        family=item.get("family", ""),
                        parent_id=item.get("parent_id"),
                    )
                )
            return products
        except Exception:  # noqa: BLE001
            return None

    def set_products(self, products: list[ProductItem]) -> None:
        """Saves products catalog to cache."""
        if not self.enabled:
            return
        path = os.path.join(self.catalog_dir, "products.json")
        payload = {
            "cached_at": time.time(),
            "products": [asdict(p) for p in products],
        }
        self._write_json_atomic(path, payload)

    def clear(self) -> None:
        """Clears all cached cases, lists, and catalog files."""
        if not self.enabled:
            return
        if os.path.exists(self.cases_dir):
            with contextlib.suppress(OSError):
                shutil.rmtree(self.cases_dir)
                os.makedirs(self.cases_dir, exist_ok=True)
        if os.path.exists(self.lists_dir):
            with contextlib.suppress(OSError):
                shutil.rmtree(self.lists_dir)
                os.makedirs(self.lists_dir, exist_ok=True)
        if os.path.exists(self.catalog_dir):
            with contextlib.suppress(OSError):
                shutil.rmtree(self.catalog_dir)
                os.makedirs(self.catalog_dir, exist_ok=True)

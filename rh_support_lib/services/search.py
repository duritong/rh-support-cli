from __future__ import annotations

from typing import Any

from rh_support_lib.api import AuthenticationError
from rh_support_lib.constants import KCS_SEARCH_URL
from rh_support_lib.errors import ReadError
from rh_support_lib.models import Case, KCSSearchResult
from rh_support_lib.services.cases import CaseService


class SearchService:
    """Unified search service for Red Hat Support Cases and Knowledgebase (KCS) content."""

    def __init__(
        self,
        api_client,
        case_service: CaseService | None = None,
        config: dict | None = None,
    ):
        self.api_client = api_client
        self.case_service = case_service or CaseService(api_client, config=config)

    def search_cases(
        self,
        query: str,
        account: str | None = None,
        status: list[str] | str | None = None,
        severity: list[str] | str | None = None,
        owner: str | None = None,
        product: str | None = None,
        limit: int = 20,
        local_only: bool = False,
        following: bool = False,
    ) -> list[Case]:
        """Searches support cases via GraphQL API and local cache."""
        if following:
            if limit <= 0:
                return []
            cases = self.case_service.list_followed_cases(
                username=self.case_service.get_current_user(local_only=True)
                if local_only
                else None,
                fetch_live=not local_only,
                account=account,
                status=status,
                severity=severity,
                owner=owner,
                product=product,
            )
            term = str(query).strip().casefold()
            return [
                case
                for case in cases
                if term in str(case.case_number or case.id).casefold()
                or term in str(case.summary or "").casefold()
            ][:limit]
        return self.case_service.search_cases(
            query=query,
            account=account,
            status=status,
            severity=severity,
            owner=owner,
            product=product,
            limit=limit,
            local_only=local_only,
        )

    def search_kcs(
        self,
        query: str,
        product: str | None = None,
        language: str | None = "en",
        limit: int = 20,
    ) -> list[KCSSearchResult]:
        """Searches Red Hat Knowledgebase (KCS) solutions and articles via the REST search API."""
        if limit <= 0 or not query or not str(query).strip():
            return []

        clean_query = str(query).strip()
        params: dict[str, Any] = {
            "q": clean_query,
            "rows": min(limit, 50),
        }
        fq_filters: list[str] = []
        if product:
            fq_filters.append(f'product:"{product.strip()}"')

        target_lang = (language or "").strip().lower()
        if target_lang and target_lang not in ("all", "*"):
            params["language"] = target_lang
            fq_filters.append(f"language:{target_lang}")

        if fq_filters:
            params["fq"] = fq_filters

        try:
            resp = self.api_client.get(KCS_SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except AuthenticationError:
            raise
        except Exception:  # noqa: BLE001 - normalize remote read failures without secrets
            raise ReadError("Knowledgebase search is unavailable.") from None

        results: list[KCSSearchResult] = []
        if isinstance(data, list):
            docs = data
        elif isinstance(data, dict):
            response = data.get("response")
            if isinstance(response, dict) and "docs" in response:
                docs = response["docs"]
            elif "docs" in data:
                docs = data["docs"]
            else:
                raise ReadError(
                    "Knowledgebase search returned an invalid result container."
                )
        else:
            raise ReadError(
                "Knowledgebase search returned an invalid result container."
            )
        if not isinstance(docs, list) or any(not isinstance(doc, dict) for doc in docs):
            raise ReadError("Knowledgebase search returned an invalid document list.")
        for doc in docs:
            doc_id = str(
                doc.get("id") or doc.get("documentId") or doc.get("solution_id") or ""
            ).strip()
            title = str(doc.get("title") or doc.get("summary") or "").strip()
            summary = str(doc.get("summary") or doc.get("abstract") or "").strip()
            doc_kind = str(
                doc.get("documentKind") or doc.get("document_kind") or "Solution"
            ).strip()
            view_uri = str(
                doc.get("view_uri") or doc.get("uri") or doc.get("url") or ""
            ).strip()
            if not title or not (doc_id or view_uri):
                raise ReadError(
                    "Knowledgebase search returned invalid document metadata."
                )

            # If doc_id is a URL or documentation path, migrate to view_uri and clear doc_id
            if doc_id.startswith(("http://", "https://", "/", "www.")) or "/" in doc_id:
                if not view_uri:
                    view_uri = doc_id
                doc_id = ""

            # Client-side language filtering to prevent untranslated/foreign results
            if target_lang and target_lang not in ("all", "*"):
                doc_lang = (
                    str(doc.get("language") or doc.get("locale") or "").strip().lower()
                )
                if doc_lang and not doc_lang.startswith(target_lang):
                    continue
                view_lower = view_uri.lower()
                if target_lang == "en" and any(
                    f"/{code}/" in view_lower
                    for code in (
                        "ja",
                        "zh",
                        "zh_cn",
                        "zh-cn",
                        "ko",
                        "de",
                        "fr",
                        "es",
                        "pt",
                        "it",
                        "ru",
                    )
                ):
                    continue
            prod_val = doc.get("product") or doc.get("products") or ""
            if isinstance(prod_val, list):
                prod_str = str(prod_val[0]) if prod_val else ""
            else:
                prod_str = str(prod_val)

            pub_date = str(
                doc.get("published_date") or doc.get("last_modified_date") or ""
            ).strip()

            results.append(
                KCSSearchResult(
                    id=doc_id,
                    title=title,
                    summary=summary,
                    document_kind=doc_kind,
                    view_uri=view_uri,
                    product=prod_str,
                    published_date=pub_date,
                )
            )
            if len(results) >= limit:
                break

        return results

    def search_all(
        self,
        query: str,
        product: str | None = None,
        language: str | None = "en",
        limit: int = 20,
        local_only: bool = False,
        account: str | None = None,
        status: list[str] | str | None = None,
        severity: list[str] | str | None = None,
        owner: str | None = None,
    ) -> dict[str, list[Any]]:
        """Searches both support cases and KCS solutions."""
        if limit <= 0:
            return {"cases": [], "kcs": []}
        cases = self.search_cases(
            query=query,
            product=product,
            limit=limit,
            local_only=local_only,
            account=account,
            status=status,
            severity=severity,
            owner=owner,
        )
        kcs = (
            []
            if local_only
            else self.search_kcs(
                query=query, product=product, language=language, limit=limit
            )
        )
        return {
            "cases": cases,
            "kcs": kcs,
        }

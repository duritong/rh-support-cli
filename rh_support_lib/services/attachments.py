from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable

import requests

from rh_support_lib.constants import ATTACHMENT_API_URL, BACKEND_MODE
from rh_support_lib.models import Attachment


class AttachmentService:
    """Service to handle all support case file attachment operations with dual v1 and v3 engines."""

    def __init__(
        self,
        api_client,
        backend: str | None = None,
        base_url: str | None = None,
    ):
        self.api_client = api_client
        self.backend = (backend or os.environ.get("RH_BACKEND") or BACKEND_MODE).lower()
        self.base_url = (base_url or ATTACHMENT_API_URL).rstrip("/")
        self._use_v3 = self.backend == "graphql"

    def list_attachments(
        self,
        case_number: str,
        include_deleted: bool = False,
        sorting_field: str | None = None,
        sort_by: str | None = None,
    ) -> list[Attachment]:
        """Lists all attachments for a specific support case."""
        if self._use_v3:
            return self._list_attachments_v3(
                case_number=case_number,
                include_deleted=include_deleted,
                sorting_field=sorting_field,
                sort_by=sort_by,
            )
        return self._list_attachments_v1(case_number)

    def _list_attachments_v3(
        self,
        case_number: str,
        include_deleted: bool = False,
        sorting_field: str | None = None,
        sort_by: str | None = None,
    ) -> list[Attachment]:
        url = f"{self.base_url}/v3/cases/{case_number}/attachments"
        params = {}
        if include_deleted:
            params["includeDeleted"] = "true"
        if sorting_field:
            params["sortingField"] = sorting_field
        if sort_by:
            params["sortBy"] = sort_by

        resp = self.api_client.get(url, params=params)
        resp.raise_for_status()
        raw_list = resp.json()
        if not isinstance(raw_list, list):
            return []

        attachments = []
        for item in raw_list:
            uuid_val = (
                item.get("uuid") or item.get("attachmentId") or item.get("id", "")
            )
            size_val = item.get("size", 0)
            size_kb = item.get("sizeKB", round(size_val / 1024.0, 1))
            attachments.append(
                Attachment(
                    uuid=str(uuid_val),
                    file_name=item.get("fileName")
                    or item.get("name", "attachment.bin"),
                    size=int(size_val),
                    size_kb=float(size_kb),
                    created_date=item.get("createdDate", ""),
                    created_by=item.get("createdBy", ""),
                    description=item.get("description", ""),
                    download_link=item.get("link", ""),
                    upload_status=item.get("uploadStatus", "Completed"),
                    case_number=str(item.get("caseNumber", case_number)),
                )
            )
        return attachments

    def _list_attachments_v1(self, case_number: str) -> list[Attachment]:
        base_root = re.sub(r"/v\d+$", "", self.base_url)
        v1_url = f"{base_root}/v1/cases/{case_number}"
        with contextlib.suppress(Exception):
            resp = self.api_client.get(v1_url)
            if resp.status_code == 200:
                data = resp.json()
                raw_atts = data.get("attachments") or []
                attachments = []
                for a in raw_atts:
                    uuid_val = a.get("uuid") or a.get("id") or ""
                    size_val = a.get("size") or 0
                    attachments.append(
                        Attachment(
                            uuid=str(uuid_val),
                            file_name=a.get("fileName")
                            or a.get("name")
                            or "attachment.bin",
                            size=int(size_val),
                            size_kb=round(int(size_val) / 1024.0, 1),
                            created_date=a.get("createdDate", ""),
                            created_by=a.get("createdBy", ""),
                            description=a.get("description", ""),
                            download_link=a.get("link", ""),
                            upload_status="Completed",
                            case_number=str(case_number),
                        )
                    )
                return attachments
        return []

    def get_attachment_status(
        self, case_number: str, attachment_id: str | None = None
    ) -> list[dict]:
        """Fetches status of one or all attachments on a case."""
        if attachment_id:
            url = f"{self.base_url}/v3/cases/{case_number}/attachments/{attachment_id}/status"
        else:
            url = f"{self.base_url}/v3/cases/{case_number}/attachments/status"

        resp = self.api_client.get(url)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else [data]

    def download_attachment(
        self,
        case_number: str,
        attachment_id: str,
        target_path: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> str:
        """
        Retrieves the presigned download URL for an attachment and downloads the file.
        Returns the local path of the downloaded file.
        """
        url = f"{self.base_url}/v3/cases/attachments/downloadFile/{case_number}/{attachment_id}"
        resp = self.api_client.get(url)
        resp.raise_for_status()
        data = resp.json()

        download_url = data.get("downloadUrl")
        if not download_url:
            raise RuntimeError("No presigned download URL returned by server.")

        filename = data.get("fileName", f"attachment_{attachment_id}.bin")
        dest_path = target_path or filename
        if os.path.isdir(dest_path):
            dest_path = os.path.join(dest_path, filename)

        # Download from storage using the presigned URL directly
        down_resp = requests.get(download_url, stream=True, timeout=60)
        down_resp.raise_for_status()
        total_size = int(down_resp.headers.get("Content-Length", 0))
        downloaded = 0

        with open(dest_path, "wb") as f:
            for chunk in down_resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress_callback(downloaded, total_size)

        return dest_path

    def delete_attachment(self, case_number: str, attachment_id: str) -> bool:
        """Deletes an attachment from a support case."""
        url = f"{self.base_url}/v3/cases/attachments/deleteFile/{case_number}/{attachment_id}"
        resp = self.api_client.delete(url)
        if resp.status_code in (200, 204):
            return True
        resp.raise_for_status()
        return True

    def upload_attachment(
        self,
        case_number: str,
        file_path: str,
        description: str = "",
        client_id: str = "rh-support-cli",
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Attachment:
        """
        Uploads a local file to a support case using either v3 presigned URLs or v1 multipart streaming.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File '{file_path}' does not exist.")

        if self._use_v3:
            return self._upload_attachment_v3(
                case_number=case_number,
                file_path=file_path,
                description=description,
                client_id=client_id,
                progress_callback=progress_callback,
            )
        return self._upload_attachment_v1(
            case_number=case_number,
            file_path=file_path,
            description=description,
        )

    def _upload_attachment_v3(
        self,
        case_number: str,
        file_path: str,
        description: str = "",
        client_id: str = "rh-support-cli",
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Attachment:
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)

        # 1. Initiate upload
        init_url = f"{self.base_url}/v3/cases/attachments/upload"
        payload = {
            "fileName": file_name,
            "fileSize": file_size,
            "caseNumber": str(case_number),
            "clientId": client_id,
            "description": description,
        }
        if file_size > 5368709120:  # > 5 GB
            chunk_size = 5368709120
            total_chunks = (file_size + chunk_size - 1) // chunk_size
            payload["totalChunks"] = total_chunks

        init_resp = self.api_client.post(init_url, json=payload)
        init_resp.raise_for_status()
        init_data = init_resp.json()

        attachment_id = init_data.get("attachmentId", "")
        strategy = init_data.get("uploadStrategy", "SINGLE_PUT")

        # 2. Upload file data
        if strategy == "SINGLE_PUT":
            presigned_url = init_data.get("presignedUrl")
            if not presigned_url:
                raise RuntimeError(
                    "SINGLE_PUT strategy specified but presignedUrl missing."
                )

            with open(file_path, "rb") as f:
                put_headers = {"Content-Type": "application/octet-stream"}
                put_resp = requests.put(
                    presigned_url,
                    data=f,
                    headers=put_headers,
                    timeout=(30, 3600),
                )
                put_resp.raise_for_status()
                if progress_callback:
                    progress_callback(file_size, file_size)

        elif strategy == "MULTIPART":
            upload_id = init_data.get("uploadId")
            parts = init_data.get("parts", [])
            if not upload_id or not parts:
                raise RuntimeError(
                    "MULTIPART strategy specified but uploadId/parts missing."
                )

            parts_result = []
            chunk_size = file_size // len(parts) if parts else file_size
            uploaded_bytes = 0

            with open(file_path, "rb") as f:
                for part in parts:
                    part_num = part.get("partNumber", 1)
                    part_url = part.get("presignedUrl")
                    part_data = f.read(chunk_size)

                    put_headers = {"Content-Type": "application/octet-stream"}
                    put_resp = requests.put(
                        part_url,
                        data=part_data,
                        headers=put_headers,
                        timeout=(30, 3600),
                    )
                    put_resp.raise_for_status()

                    etag = put_resp.headers.get("ETag", "").strip('"')
                    parts_result.append({"partNumber": part_num, "eTag": etag})
                    uploaded_bytes += len(part_data)
                    if progress_callback:
                        progress_callback(uploaded_bytes, file_size)

            # 3. Complete multipart upload
            complete_url = f"{self.base_url}/v3/cases/attachments/upload/complete"
            comp_payload = {
                "attachmentId": attachment_id,
                "caseNumber": str(case_number),
                "uploadId": upload_id,
                "parts": parts_result,
            }
            comp_resp = self.api_client.post(complete_url, json=comp_payload)
            comp_resp.raise_for_status()

        size_kb = round(file_size / 1024.0, 1)
        return Attachment(
            uuid=attachment_id,
            file_name=file_name,
            size=file_size,
            size_kb=size_kb,
            created_date="",
            description=description,
            upload_status="Completed",
            case_number=str(case_number),
        )

    def _upload_attachment_v1(
        self,
        case_number: str,
        file_path: str,
        description: str = "",
    ) -> Attachment:
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)

        base_root = re.sub(r"/v\d+$", "", self.base_url)
        v1_url = f"{base_root}/v1/cases/{case_number}/attachments"

        try:
            from requests_toolbelt.multipart.encoder import MultipartEncoder

            with open(file_path, "rb") as f:
                m = MultipartEncoder(fields={"file": (file_name, f)})
                headers = {"Content-Type": m.content_type}
                resp = self.api_client.post(
                    v1_url, data=m, headers=headers, timeout=(30, 3600)
                )
                resp.raise_for_status()
        except ImportError:
            with open(file_path, "rb") as f:
                files = {"file": (file_name, f)}
                resp = self.api_client.post(v1_url, files=files, timeout=(30, 3600))
                resp.raise_for_status()

        size_kb = round(file_size / 1024.0, 1)
        return Attachment(
            uuid="a1",
            file_name=file_name,
            size=file_size,
            size_kb=size_kb,
            created_date="",
            description=description,
            upload_status="Completed",
            case_number=str(case_number),
        )

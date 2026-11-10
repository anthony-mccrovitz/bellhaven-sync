"""Thin client for the CRM sandbox API."""
from __future__ import annotations

import time

import requests

from .config import CRM_BASE_URL, CRM_TOKEN

MUTABLE_ACCOUNT_FIELDS = [
    "name", "parent_id", "status", "note", "care_type", "phone", "billing_street",
    "billing_city", "billing_state", "billing_zip", "chow_current_account",
    "duplicate_of_account",
]


class CRMError(RuntimeError):
    pass


class CRM:
    def __init__(self, token: str | None = None, base_url: str | None = None):
        self.base = (base_url or CRM_BASE_URL).rstrip("/") + "/api/v1"
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token or CRM_TOKEN}",
            "Content-Type": "application/json",
        })

    def _req(self, method: str, path: str, retries: int = 4, **kw):
        """GETs are retried on 5xx / connection errors (the sandbox occasionally 500s).
        Writes are never retried blindly: a timeout on a POST could have landed."""
        last = None
        for attempt in range(retries if method == "GET" else 1):
            try:
                r = self.s.request(method, f"{self.base}{path}", timeout=30, **kw)
            except requests.RequestException as e:
                last = CRMError(f"{method} {path} -> {e}")
            else:
                if r.status_code < 400:
                    return r.json()
                last = CRMError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
                if r.status_code < 500:
                    break
            time.sleep(1.5 * (attempt + 1))
        raise last

    def me(self) -> dict:
        return self._req("GET", "/me")

    def _paged(self, path: str, **params) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            d = self._req("GET", path, params={**params, "page": page, "page_size": 50})
            out.extend(d["data"])
            if len(out) >= d.get("total", 0) or not d["data"]:
                return out
            page += 1

    def list_accounts(self, **params) -> list[dict]:
        return self._paged("/accounts", **params)

    def list_contacts(self, **params) -> list[dict]:
        return self._paged("/contacts", **params)

    def get_account(self, account_id: str) -> dict:
        return self._req("GET", f"/accounts/{account_id}")

    def update_account(self, account_id: str, fields: dict) -> dict:
        bad = set(fields) - set(MUTABLE_ACCOUNT_FIELDS)
        if bad:
            raise CRMError(f"not mutable: {sorted(bad)}")
        return self._req("PATCH", f"/accounts/{account_id}", json=fields)

    def create_account(self, fields: dict) -> dict:
        if not fields.get("name"):
            raise CRMError("name is required")
        return self._req("POST", "/accounts", json=fields)

    def create_contact(self, fields: dict) -> dict:
        return self._req("POST", "/contacts", json=fields)

"""
Business Central API client for target-booksy.

Two-step flow (mirrors target-intacct session pattern):
  Step 1 — _authenticate()
            POST https://login.microsoft.com/{tenant}/oauth2/v2.0/token
            grant_type=client_credentials
            → stores Bearer access_token + expiry

  Step 2 — post_batch(payloads)
            POST https://api.businesscentral.dynamics.com/.../$batch
            Authorization: Bearer <access_token>
            Body: { "requests": [ up to 100 bc_gj_lines entries ] }
            → creates up to 100 general journal lines in one API call
            → caller loops in chunks of BATCH_SIZE if total > 100
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
import singer

from .const import (
    AUTH_URL_TEMPLATE,
    BC_BATCH_ITEM_URL_TEMPLATE,
    BC_BATCH_URL_TEMPLATE,
    BC_SCOPE,
    BATCH_SIZE,
    DEFAULT_TIMEOUT_SECONDS,
    GRANT_TYPE,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
)
from .exceptions import (
    AuthenticationError,
    EntryPostError,
    RateLimitError,
    ServerError,
    TokenExpiredError,
)

logger = singer.get_logger()


class BusinessCentralClient:
    """
    Authenticates via Microsoft OAuth2 (client_credentials) and bulk-posts
    general journal lines to the Business Central Chargebee $batch API.

    Two-step flow (mirrors target-intacct):
      __init__     → _authenticate()   (getAPISession equivalent)
      post_batch() → $batch endpoint   (up to 100 records per call)

    Token refresh: 401 mid-run triggers one automatic re-auth + retry.
    """

    def __init__(
        self,
        tenant_domain: str,
        client_id: str,
        client_secret: str,
        environment: str,
        company_id: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._tenant_domain  = tenant_domain
        self._client_id      = client_id
        self._client_secret  = client_secret
        self._environment    = environment
        self._company_id     = company_id
        self._timeout        = timeout

        self._access_token: Optional[str]      = None
        self._token_expiry: Optional[datetime] = None

        self._auth_url  = AUTH_URL_TEMPLATE.format(tenant_domain=tenant_domain)
        self._batch_url = BC_BATCH_URL_TEMPLATE.format(
            tenant_domain=tenant_domain,
            environment=environment,
        )
        self._batch_item_url = BC_BATCH_ITEM_URL_TEMPLATE.format(
            company_id=company_id,
        )

        self._session = requests.Session()

        # Authenticate immediately — fail fast on bad credentials
        self._authenticate()

    # ── Step 1: Authentication ──────────────────────────────────────────────────

    def _authenticate(self) -> None:
        """
        POST to Microsoft OAuth2 token endpoint using client_credentials.
        Stores Bearer token and expiry. Equivalent to target-intacct _set_session_id().
        """
        logger.info("Authenticating with Microsoft OAuth2 (client_credentials)...")

        payload = {
            "grant_type":    GRANT_TYPE,
            "client_id":     self._client_id,
            "client_secret": self._client_secret,
            "scope":         BC_SCOPE,
        }

        try:
            response = self._session.post(
                self._auth_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self._timeout,
            )
        except requests.exceptions.ConnectionError as exc:
            raise AuthenticationError(
                f"Cannot connect to auth endpoint: {self._auth_url}. Error: {exc}"
            ) from exc

        if response.status_code != 200:
            raise AuthenticationError(
                f"OAuth2 token request failed [{response.status_code}]: {response.text}"
            )

        body = response.json()
        if "access_token" not in body:
            raise AuthenticationError(
                f"OAuth2 response missing access_token. Response: {body}"
            )

        self._access_token = body["access_token"]
        expires_in = int(body.get("expires_in", 3599))
        self._token_expiry = datetime.utcnow() + timedelta(seconds=expires_in - 60)

        logger.info(
            "Authentication successful. Token valid for ~%d minutes.", expires_in // 60
        )

        self._session.headers.update({
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        })

    def _ensure_valid_token(self) -> None:
        """Re-authenticate if the token has expired or is close to expiry."""
        if self._token_expiry and datetime.utcnow() >= self._token_expiry:
            logger.warning("Bearer token expired. Re-authenticating...")
            self._authenticate()

    # ── Step 2: Bulk batch POST ─────────────────────────────────────────────────

    def post_batch(
        self, payloads: List[Dict[str, Any]]
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        POST up to BATCH_SIZE (100) bc_gj_lines in a single $batch call.

        Builds the OData $batch envelope:
        {
          "requests": [
            { "method": "POST", "id": "1", "url": "companies(...)/bc_gj_lines", "body": {...} },
            { "method": "POST", "id": "2", "url": "companies(...)/bc_gj_lines", "body": {...} },
            ...
          ]
        }

        Parameters
        ----------
        payloads : list of dict
            List of bc_gj_lines body dicts (max BATCH_SIZE items).

        Returns
        -------
        succeeded : list of dict
            Response bodies for records that returned status 201.
        failed : list of dict
            Dicts with {"id", "status", "error"} for non-201 responses.

        Raises
        ------
        EntryPostError    : on HTTP-level 4xx (not per-record)
        TokenExpiredError : on 401 after re-auth
        RateLimitError    : on 429 after MAX_RETRIES
        ServerError       : on 5xx after MAX_RETRIES
        """
        self._ensure_valid_token()

        # Build $batch envelope
        batch_requests = [
            {
                "method": "POST",
                "id":     str(i + 1),
                "url":    self._batch_item_url,
                "body":   payload,
            }
            for i, payload in enumerate(payloads)
        ]
        body = {"requests": batch_requests}

        delay = RETRY_BACKOFF_BASE

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._session.post(
                    self._batch_url,
                    json=body,
                    timeout=self._timeout,
                )

                if response.status_code == 200:
                    return self._parse_batch_response(response.json())

                elif response.status_code == 401:
                    if attempt == 0:
                        logger.warning("401 Unauthorized. Re-authenticating and retrying...")
                        self._authenticate()
                        continue
                    raise TokenExpiredError(
                        "401 Unauthorized after re-authentication attempt.",
                        response=response.text,
                    )

                elif response.status_code == 429:
                    if attempt < MAX_RETRIES:
                        logger.warning(
                            "Rate limited (429). Retrying in %ds (attempt %d/%d)...",
                            delay, attempt + 1, MAX_RETRIES,
                        )
                        time.sleep(delay)
                        delay *= 2
                        continue
                    raise RateLimitError(
                        f"Batch rate limited after {MAX_RETRIES} retries.",
                        response=response.text,
                    )

                elif response.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        logger.warning(
                            "Server error %d. Retrying in %ds (attempt %d/%d)...",
                            response.status_code, delay, attempt + 1, MAX_RETRIES,
                        )
                        time.sleep(delay)
                        delay *= 2
                        continue
                    raise ServerError(
                        f"Batch server error {response.status_code} after {MAX_RETRIES} retries.",
                        response=response.text,
                    )

                else:
                    raise EntryPostError(
                        f"$batch API error [{response.status_code}]: {response.text}",
                        response=response.text,
                    )

            except requests.exceptions.Timeout:
                if attempt < MAX_RETRIES:
                    logger.warning(
                        "Batch request timed out. Retrying in %ds (attempt %d/%d)...",
                        delay, attempt + 1, MAX_RETRIES,
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise EntryPostError(
                    f"Batch request timed out after {MAX_RETRIES} retries."
                )

            except requests.exceptions.ConnectionError as exc:
                raise EntryPostError(
                    f"Cannot connect to BC $batch API: {self._batch_url}. Error: {exc}"
                ) from exc

        raise EntryPostError("Batch POST failed after all retries.")

    # ── Response parsing ────────────────────────────────────────────────────────

    def _parse_batch_response(
        self, response_body: Dict
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Parse the $batch response and separate successes from failures.

        Each item in responses[] has:
          - "id"     : matches the request id (1-based string)
          - "status" : HTTP status code (201 = success, others = failure)
          - "body"   : the created record or error detail

        Returns
        -------
        succeeded : list of response body dicts (status 201)
        failed    : list of {"id", "status", "error"} dicts (non-201)
        """
        succeeded = []
        failed    = []

        for item in response_body.get("responses", []):
            item_id = item.get("id", "?")
            status  = item.get("status")
            body    = item.get("body", {})

            if status == 201:
                logger.info(
                    "  [id=%s] Created: bc_id=%s ext_doc=%s account=%s amount=%s",
                    item_id,
                    body.get("id"),
                    body.get("ext_document_no"),
                    body.get("account_no"),
                    body.get("amount"),
                )
                succeeded.append(body)
            else:
                error_msg = body.get("error", {}).get("message", str(body)) if isinstance(body, dict) else str(body)
                logger.error(
                    "  [id=%s] FAILED status=%s: %s", item_id, status, error_msg
                )
                failed.append({"id": item_id, "status": status, "error": error_msg})

        return succeeded, failed


# ── Factory ──────────────────────────────────────────────────────────────────────

def get_client(
    *,
    tenant_domain: str,
    client_id: str,
    client_secret: str,
    environment: str,
    company_id: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> BusinessCentralClient:
    """Instantiate and return an authenticated BusinessCentralClient."""
    return BusinessCentralClient(
        tenant_domain=tenant_domain,
        client_id=client_id,
        client_secret=client_secret,
        environment=environment,
        company_id=company_id,
        timeout=timeout,
    )

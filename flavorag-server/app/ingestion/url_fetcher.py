from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Awaitable, Callable
from urllib.parse import unquote, urljoin, urlparse

import httpx


class URLSecurityError(ValueError):
    pass


@dataclass(frozen=True)
class FetchedURL:
    content: bytes
    final_url: str
    content_type: str
    etag: str
    last_modified: str
    filename: str


async def _default_resolver(hostname: str) -> list[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return sorted({record[4][0] for record in records})


class SafeURLFetcher:
    def __init__(
        self,
        *,
        resolver: Callable[[str], Awaitable[list[str]]] = _default_resolver,
        max_bytes: int = 50 * 1024 * 1024,
        timeout_sec: float = 120.0,
        max_redirects: int = 5,
        allow_private_networks: bool = False,
    ):
        self.resolver = resolver
        self.max_bytes = max(1, max_bytes)
        self.timeout_sec = timeout_sec
        self.max_redirects = max_redirects
        self.allow_private_networks = allow_private_networks

    async def validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise URLSecurityError("only http and https URLs are allowed")
        if parsed.username or parsed.password:
            raise URLSecurityError("URL credentials are not allowed")
        if not parsed.hostname:
            raise URLSecurityError("URL hostname is required")
        try:
            addresses = await self.resolver(parsed.hostname)
        except Exception as exc:
            raise URLSecurityError("URL hostname could not be resolved") from exc
        if not addresses:
            raise URLSecurityError("URL hostname has no address")
        if self.allow_private_networks:
            return
        for raw in addresses:
            address = ipaddress.ip_address(raw)
            if not address.is_global:
                raise URLSecurityError("private or reserved URL targets are not allowed")

    async def fetch(self, url: str) -> FetchedURL:
        current = url.strip()
        async with httpx.AsyncClient(timeout=self.timeout_sec, follow_redirects=False) as client:
            for redirect_count in range(self.max_redirects + 1):
                await self.validate_url(current)
                async with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect_count >= self.max_redirects:
                            raise URLSecurityError("too many redirects")
                        location = response.headers.get("location")
                        if not location:
                            raise URLSecurityError("redirect has no location")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > self.max_bytes:
                        raise URLSecurityError("URL response exceeds size limit")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise URLSecurityError("URL response exceeds size limit")
                        chunks.append(chunk)
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    filename = _filename_from_response(current, response.headers)
                    return FetchedURL(
                        content=b"".join(chunks),
                        final_url=current,
                        content_type=content_type,
                        etag=response.headers.get("etag", ""),
                        last_modified=response.headers.get("last-modified", ""),
                        filename=filename,
                    )
        raise URLSecurityError("URL fetch did not complete")


def _filename_from_response(url: str, headers) -> str:
    content_disposition = headers.get("content-disposition", "")
    marker = "filename="
    if marker in content_disposition.lower():
        value = content_disposition.split("=", 1)[1].strip().strip("\"'")
        name = PurePosixPath(unquote(value)).name
        if name:
            return name
    name = PurePosixPath(unquote(urlparse(url).path)).name
    return name or "document"


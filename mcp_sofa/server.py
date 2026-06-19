# SPDX-FileCopyrightText: 2026 Peter Lemenkov <lemenkov@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""MCP server for Stack Overflow for Agents."""

import argparse
import asyncio
import logging
import os
import sys
from typing import Any, Optional

import httpx
from fastmcp import FastMCP

SOFA_BASE_URL = os.environ.get("SOFA_BASE_URL", "https://agents.stackoverflow.com")
SOFA_API_KEY = os.environ.get("SOFA_API_KEY", "")

mcp = FastMCP("Stack Overflow for Agents")

# Module-level session state
_session_id: Optional[str] = None
_client: Optional[httpx.AsyncClient] = None


def _headers(with_session: bool = True) -> dict[str, str]:
    """Build request headers."""
    h = {
        "Authorization": f"Bearer {SOFA_API_KEY}",
        "X-Sofa-Client-Name": "mcp-sofa",
        "X-Sofa-Model-Name": "claude-sonnet-4-6",
        "X-Sofa-Model-Provider": "anthropic",
        "X-Sofa-Model-Selection-Mode": "fixed",
    }
    if with_session and _session_id:
        h["X-Sofa-Session"] = _session_id
    return h


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=SOFA_BASE_URL, timeout=30.0)
    return _client


async def _ensure_session() -> None:
    """Create a session if one doesn't exist."""
    global _session_id
    if _session_id:
        return
    client = await _get_client()
    resp = await client.post("/api/sessions", headers=_headers(with_session=False))
    resp.raise_for_status()
    _session_id = resp.json()["session_id"]


async def _request(
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Make an authenticated request, ensuring a session exists."""
    await _ensure_session()
    client = await _get_client()
    headers = _headers()
    if "json" in kwargs:
        headers["Content-Type"] = "application/json"
    resp = await client.request(method, path, headers=headers, **kwargs)
    if resp.status_code == 401:
        # Session expired — reset and retry once
        global _session_id
        _session_id = None
        await _ensure_session()
        resp = await client.request(method, path, headers=_headers(), **kwargs)
    resp.raise_for_status()
    if resp.status_code == 204:
        return {"status": "ok"}
    return resp.json()


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool(
    tags={"read"},
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
async def sofa_search(
    query: str,
    tag: Optional[str] = None,
    content_type: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """Search Stack Overflow for Agents for validated knowledge before brute-forcing.

    Args:
        query: Search terms
        tag: Filter by tag (e.g. 'python', 'erlang')
        content_type: Filter by type: question, til, blueprint (omit for all)
        page: Page number (default 1)
        per_page: Results per page (max 100, default 20)
    """
    params: dict[str, Any] = {"search": query, "page": page, "per_page": per_page}
    if tag:
        params["tag"] = tag
    if content_type:
        params["content_type"] = content_type
    return await _request("GET", "/api/posts", params=params)


@mcp.tool(
    tags={"read"},
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
async def sofa_get_post(post_id: str) -> dict:
    """Get full content of a Stack Overflow for Agents post including replies and trust summary.

    Args:
        post_id: UUID of the post
    """
    return await _request("GET", f"/api/posts/{post_id}")


@mcp.tool(
    tags={"write"},
    annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
)
async def sofa_create_post(
    content_type: str,
    title: str,
    body: str,
    tags: list[str],
) -> dict:
    """Create a new post on Stack Overflow for Agents.

    Args:
        content_type: Post type: question, til, or blueprint
        title: Post title (max 200 chars)
        body: Post body in Markdown (max 50000 chars)
        tags: List of tags (max 8, each max 50 chars)
    """
    return await _request("POST", "/api/posts", json={
        "content_type": content_type,
        "title": title,
        "body": body,
        "tags": tags,
    })


@mcp.tool(
    tags={"write"},
    annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
)
async def sofa_reply(post_id: str, body: str) -> dict:
    """Post a reply to a Stack Overflow for Agents post.

    Args:
        post_id: UUID of the post to reply to
        body: Reply body in Markdown (max 25000 chars)
    """
    return await _request("POST", f"/api/posts/{post_id}/replies", json={"body": body})


@mcp.tool(
    tags={"write"},
    annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
)
async def sofa_vote(post_id: str, value: int) -> dict:
    """Vote on a Stack Overflow for Agents post (must have read it first).

    Args:
        post_id: UUID of the post to vote on
        value: 1 for upvote, -1 for downvote
    """
    return await _request("POST", "/api/votes", json={"post_id": post_id, "value": value})


@mcp.tool(
    tags={"write"},
    annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
)
async def sofa_verify(
    post_id: str,
    outcome: str,
    feedback: str,
) -> dict:
    """Report verification outcome after applying a post's guidance to a real task.

    Args:
        post_id: UUID of the post that was applied
        outcome: worked_as_written, worked_with_changes, or did_not_work
        feedback: What you applied or observed (max 500 chars, required)
    """
    return await _request("POST", "/api/verifications", json={
        "post_id": post_id,
        "outcome": outcome,
        "feedback": feedback,
    })


@mcp.tool(
    tags={"read"},
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
async def sofa_list_tags() -> dict:
    """List available tags on Stack Overflow for Agents."""
    return await _request("GET", "/api/tags")


@mcp.tool(
    tags={"read"},
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
async def sofa_leaderboard(limit: int = 20) -> dict:
    """Show top agents on Stack Overflow for Agents by reputation.

    Args:
        limit: Number of agents to return (max 100, default 20)
    """
    return await _request("GET", "/api/agents/leaderboard", params={"limit": limit})


@mcp.tool(
    tags={"write"},
    annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
)
async def sofa_delete_post(post_id: str) -> dict:
    """Delete a post you authored on Stack Overflow for Agents. This is permanent.

    Args:
        post_id: UUID of the post to delete
    """
    return await _request("DELETE", f"/api/posts/{post_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP server for Stack Overflow for Agents")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8814")))
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("MCP_TRANSPORT", "streamable-http"),
    )
    args = parser.parse_args()

    if not SOFA_API_KEY:
        print("ERROR: SOFA_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    # Set up logging
    try:
        from systemd.journal import JournalHandler
        handler: logging.Handler = JournalHandler(SYSLOG_IDENTIFIER="mcp-sofa")
    except ImportError:
        handler = logging.StreamHandler(sys.stderr)

    logging.basicConfig(handlers=[handler], level=logging.INFO)

    if args.transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
        )
    else:
        mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()

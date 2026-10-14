"""Create drafts in Buffer (https://developers.buffer.com). Shared with clp-daily-verse."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any
import urllib.error
import urllib.request

API_URL = "https://api.buffer.com"

CREATE_POST = """
mutation CreateDraft($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id status } }
    ... on MutationError { message }
  }
}
"""


class BufferError(Exception):
    """Buffer rejected the request."""


def video_metadata(service: str, title: str) -> dict[str, Any]:
    """Settings for a Reel (Facebook, Instagram) or a YouTube Short."""
    return {
        "facebook": {"facebook": {"type": "reel"}},
        "instagram": {"instagram": {"type": "reel", "shouldShareToFeed": True}},
        # 22 = People & Blogs. Vertical videos under three minutes become Shorts.
        "youtube": {"youtube": {"title": title, "categoryId": "22", "privacy": "public", "madeForKids": False}},
    }[service]


def _request(api_key: str, query: str, variables: dict) -> dict:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.load(response)
    except urllib.error.HTTPError as err:
        raise BufferError(f"Buffer returned HTTP {err.code}: {err.read().decode(errors='replace')}") from err

    if body.get("errors"):
        raise BufferError("; ".join(e.get("message", str(e)) for e in body["errors"]))
    return body.get("data") or {}


def _graphql(api_key: str, query: str, variables: dict, field: str) -> dict:
    result = _request(api_key, query, variables).get(field) or {}
    if "post" not in result:
        raise BufferError(result.get("message", f"Unexpected response: {result}"))
    return result["post"]


def create_draft(
    api_key: str,
    channel_id: str,
    text: str,
    assets: list[dict[str, Any]],
    metadata: dict[str, Any],
    due_at: datetime,
) -> str:
    """Save a post as a draft and return its Buffer post id.

    `due_at` pre-fills the publish time shown when the draft is approved.
    """
    post_input = {
        "channelId": channel_id,
        "text": text,
        "schedulingType": "automatic",
        "mode": "customScheduled",
        "dueAt": due_at.isoformat(),
        "saveToDraft": True,
        "assets": assets,
        "metadata": metadata,
    }
    return _graphql(api_key, CREATE_POST, {"input": post_input}, "createPost")["id"]


EDIT_POST = """
mutation EditDraft($input: EditPostInput!) {
  editPost(input: $input) {
    ... on PostActionSuccess { post { id status } }
    ... on MutationError { message }
  }
}
"""


def update_post(api_key: str, post_id: str, text: str, assets: list[dict[str, Any]], metadata: dict[str, Any]) -> str:
    """Replace a post's text, media and settings, keeping its time and whether it is approved."""
    post = _graphql(api_key, EDIT_POST,
                    {"input": {"id": post_id, "text": text, "assets": assets, "metadata": metadata}}, "editPost")
    return post["status"]


def post_status(api_key: str, post_id: str) -> str:
    """"draft", "scheduled" (approved), "sent", ..."""
    query = f"query {{ post(input: {{id: {json.dumps(post_id)}}}) {{ id status }} }}"
    post = _request(api_key, query, {}).get("post")
    if not post:
        raise BufferError(f"Buffer has no post {post_id}")
    return post["status"]

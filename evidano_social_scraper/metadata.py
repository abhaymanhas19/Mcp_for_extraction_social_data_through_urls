import asyncio
import re
import time
from typing import Any
import httpx

from .js import JsUrl, encode_uri_component, form_encode, is_finite_num, js_str, js_trim
from .types import JsonRecord

FIELD_ALIASES: dict[str, list[str]] = {
    "text": ["caption", "post_title"], "contentId": ["status_id", "post_id", "video_id", "media_id", "shortcode"],
    "contentUrl": ["status_url", "post_url", "video_url", "permalink"], "authorName": ["owner"],
    "authorHandle": ["username"], "authorUrl": ["profile_url"], "thumbnailUrl": ["thumbnail", "display_url"],
    "publishedAt": ["published_at"], "mediaType": ["product_type"], "durationSeconds": ["video_duration"],
    "viewCount": ["video_view_count", "impression_count", "analytics_views"], "likeCount": ["favorite_count", "like_count", "likes", "analytics_likes"],
    "commentCount": ["comment_count"], "shareCount": ["share_count", "analytics_shares"], "repostCount": ["repost_count", "retweet_count"],
    "quoteCount": ["quote_count"], "bookmarkCount": ["bookmark_count"], "language": ["language"], "location": ["location_name", "location"],
}

POST_LEVEL_ALIASES: dict[str, list[str]] = {
    "text": ["post_title", "video_title"], "contentId": ["post_id", "video_id", "media_id", "shortcode", "status_id"],
    "contentUrl": ["post_url", "video_url", "status_url"],
}
POST_LEVEL_FIELDS = ["subreddit", "post_id", "video_id", "media_id", "shortcode"]


def _first_value(records: list[JsonRecord], keys: list[str]) -> Any:
    for record in records:
        for key in keys:
            if record.get(key) is not None and record.get(key) != "":
                return record[key]
    return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and js_trim(value) else None


def _number(value: Any) -> int | float | None:
    return value if is_finite_num(value) else None


def _tags(value: str | None, marker: str) -> list[str]:
    if not value:
        return []
    pattern = r"#\w+" if marker == "#" else r"@[\w.-]+"
    return list(dict.fromkeys(re.findall(pattern, value)))


Result = tuple[dict[str, Any] | None, str | None]


class MetadataClient:
    def __init__(self, timeout_ms: int, client: httpx.AsyncClient | None = None):
        self.timeout_ms = timeout_ms
        self.client = client or httpx.AsyncClient(timeout=None)
        self._cache: dict[str, tuple[float, asyncio.Future[Result]]] = {}

    def _endpoint(self, source_url: str, platform: str) -> str | None:
        encoded = encode_uri_component(source_url)
        if platform == "youtube":
            return f"https://www.youtube.com/oembed?url={encoded}&format=json"
        if platform == "tiktok":
            return f"https://www.tiktok.com/oembed?url={encoded}"
        if platform == "x":
            return f"https://publish.twitter.com/oembed?url={encoded}&omit_script=true"
        if platform == "reddit":
            return f"https://www.reddit.com/oembed?url={encoded}"
        if platform == "vimeo":
            return f"https://vimeo.com/api/oembed.json?url={encoded}"
        return None

    async def get_oembed(self, source_url: str, platform: str) -> Result:
        key = f"{platform}|{source_url}"
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < 3_600:
            return await asyncio.shield(hit[1])
        future = asyncio.ensure_future(self._fetch_oembed(source_url, platform))
        self._cache[key] = (time.monotonic(), future)

        def drop_empty(done: asyncio.Future[Result]) -> None:
            if done.cancelled() or done.exception() or not done.result()[0]:
                if self._cache.get(key, (0, None))[1] is done:
                    del self._cache[key]

        future.add_done_callback(drop_empty)
        if len(self._cache) > 500:
            del self._cache[next(iter(self._cache))]
        return await asyncio.shield(future)

    async def _get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        response = await self.client.get(url, headers=headers or {})
        if not response.is_success:
            return response.status_code, None
        return response.status_code, response.json()

    async def _fetch_oembed(self, source_url: str, platform: str) -> Result:
        if platform == "bluesky":
            return await self._get_bluesky(source_url)
        endpoint = self._endpoint(source_url, platform)
        if not endpoint:
            return None, "Public oEmbed enrichment is not available for this platform."
        try:
            async with asyncio.timeout(min(self.timeout_ms, 10_000) / 1000):
                status, data = await self._get_json(endpoint, {"user-agent": "EvidanoMetadata/1.1"})
            if data is None and not 200 <= status < 300:
                return None, f"Public metadata enrichment returned HTTP {status}."
            return (data if isinstance(data, dict) else None), None
        except Exception:
            return None, "Public metadata enrichment was unavailable."

    async def _get_bluesky(self, source_url: str) -> Result:
        match = re.match(r"^/profile/([^/]+)/post/([^/]+)", JsUrl(source_url).pathname)
        if not match:
            return None, "Post metadata is available for a single Bluesky post URL."
        actor, post_id = match.group(1), match.group(2)
        try:
            async with asyncio.timeout(min(self.timeout_ms, 10_000) / 1000):
                status, profile = await self._get_json(f"https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={form_encode(actor)}")
                if profile is None and not 200 <= status < 300:
                    return None, f"Bluesky profile lookup returned HTTP {status}."
                if not isinstance(profile, dict) or not isinstance(profile.get("did"), str):
                    return None, "Bluesky profile lookup omitted the account identifier."
                uri = f"at://{profile['did']}/app.bsky.feed.post/{post_id}"
                status, payload = await self._get_json(f"https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts?uris={form_encode(uri)}")
                if payload is None and not 200 <= status < 300:
                    return None, f"Bluesky post lookup returned HTTP {status}."
            posts = payload.get("posts") if isinstance(payload, dict) else None
            post = posts[0] if isinstance(posts, list) and posts else None
            if not post:
                return None, "Bluesky returned no matching post metadata."
            record = post.get("record") if isinstance(post.get("record"), dict) else {}
            author = post.get("author") if isinstance(post.get("author"), dict) else {}
            handle = author.get("handle")
            return {
                "author_name": author.get("displayName"), "author_handle": handle,
                "author_url": f"https://bsky.app/profile/{js_str(handle) if handle is not None else actor}",
                "thumbnail_url": author.get("avatar"), "type": "post", "text": record.get("text"), "content_id": post_id, "content_url": source_url,
                "published_at": record.get("createdAt"), "like_count": post.get("likeCount"), "comment_count": post.get("replyCount"),
                "repost_count": post.get("repostCount"), "quote_count": post.get("quoteCount"),
                "language": record["langs"][0] if isinstance(record.get("langs"), list) and record["langs"] else None,
            }, None
        except Exception:
            return None, "Bluesky public metadata enrichment was unavailable."


async def build_content_metadata(source_url: str, platform: str, records: list[JsonRecord], client: MetadataClient, rows_are_comments: bool = False) -> dict[str, Any]:
    embed, note = await client.get_oembed(source_url, platform)
    embed = embed or {}

    def value(key: str) -> Any:
        return _first_value(records, POST_LEVEL_ALIASES.get(key, []) if rows_are_comments else FIELD_ALIASES[key])

    candidate_text = _text(embed.get("text")) or _text(value("text"))
    title = _text(embed.get("title")) or (candidate_text if platform == "reddit" else None)
    visible_text = None if platform == "reddit" and title == candidate_text else candidate_text
    additional_fields: JsonRecord = {}
    keys = POST_LEVEL_FIELDS if rows_are_comments else ["subreddit", "post_id", "video_id", "media_id", "shortcode", "profile_id", "profile_bio", "reaction_breakdown", "reactions", "images"]
    for key in keys:
        found = _first_value(records, [key])
        if found is not None:
            additional_fields[key] = found

    def pick_num(*values: Any) -> int | float | None:
        return next((n for n in (_number(v) for v in values) if n is not None), None)

    return {
        "sourceUrl": source_url, "platform": platform, "title": title, "text": visible_text,
        "contentId": _text(embed.get("content_id")) or _text(value("contentId")),
        "contentUrl": _text(embed.get("content_url")) or _text(value("contentUrl")) or source_url,
        "authorName": _text(embed.get("author_name")) or _text(value("authorName")),
        "authorHandle": _text(embed.get("author_handle")) or _text(value("authorHandle")),
        "authorUrl": _text(embed.get("author_url")) or _text(value("authorUrl")),
        "thumbnailUrl": _text(embed.get("thumbnail_url")) or _text(value("thumbnailUrl")),
        "publishedAt": _text(embed.get("published_at")) or _text(value("publishedAt")),
        "mediaType": _text(embed.get("type")) or _text(value("mediaType")),
        "durationSeconds": _number(value("durationSeconds")),
        "viewCount": _number(value("viewCount")),
        "likeCount": pick_num(embed.get("like_count"), value("likeCount")),
        "commentCount": pick_num(embed.get("comment_count"), value("commentCount")),
        "shareCount": _number(value("shareCount")),
        "repostCount": pick_num(embed.get("repost_count"), value("repostCount")),
        "quoteCount": pick_num(embed.get("quote_count"), value("quoteCount")),
        "bookmarkCount": _number(value("bookmarkCount")),
        "language": _text(embed.get("language")) or _text(value("language")),
        "location": _text(value("location")),
        "hashtags": _tags(title or visible_text, "#"),
        "mentions": _tags(title or visible_text, "@"),
        "additionalFields": additional_fields,
        "availabilityNotes": [n for n in (note, None if records else "No usable export records were available for row-based metadata.") if n],
    }

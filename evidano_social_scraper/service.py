import asyncio
import math
import re
import time
from typing import Any

from .cleaning import clean_records_for_output
from .errors import PublicError
from .js import JsUrl, is_finite_num, js_round, js_str, js_trim
from .metadata import MetadataClient, build_content_metadata
from .normalize import normalize_threads
from .platforms import PLATFORM_BY_ID, mode_status, url_form_guidance
from .types import AppConfig, JsonRecord, StartExportInput
from .upstream import ProviderError, UpstreamClient
from .validation import UrlResolution, resolve_source_url, validate_guid, validate_source_url

TERMINAL = {"done", "completed", "error", "failed", "cancelled", "stopped"}
RUNNING = {"queueing", "queued", "progress", "processing", "requeued"}
REQUEST_BUDGET_MS = 50_000
RESOLVE_BUDGET_MS = 8_000
RECORD_CACHE_TTL_MS = 10 * 60_000
RECORD_CACHE_MAX = 24
COMMENT_MODES = {"comments", "reviews", "discussions", "messages", "live_chat"}


class UrlAdjustedProviderError(ProviderError):
    def __init__(self, cause: ProviderError, resolution: UrlResolution):
        super().__init__(cause.message, cause.status, cause.retry_after_seconds)
        self.resolution = resolution


def _now_ms() -> float:
    return time.monotonic() * 1000


def _number_or_null(value: Any) -> int | float | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", js_trim(value)):
        number = float(js_trim(value))
        return int(number) if number.is_integer() else number
    return value if is_finite_num(value) else None


def _options(job: JsonRecord) -> JsonRecord:
    options = job.get("options")
    return options if isinstance(options, dict) else {}


def _text_or_null(value: Any) -> str | None:
    return value if isinstance(value, str) and js_trim(value) else None


def _job_mode(job: JsonRecord) -> str | None:
    options = _options(job)
    if options.get("live"):
        return "live_chat"
    if options.get("facebookAds"):
        return "facebook_ads"
    for mode in ("likes", "shares", "followers", "following", "tweets"):
        if options.get(mode):
            return mode
    return _text_or_null(job.get("export_type"))


def _job_platform(job: JsonRecord) -> str | None:
    url = _text_or_null(job.get("url"))
    if url:
        try:
            return validate_source_url(url)[1]
        except Exception:
            pass
    platform = _text_or_null(job.get("platform"))
    return platform.replace("twitter", "x", 1) if platform else None


def _error_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if value and isinstance(value, (dict, list)):
        record = value if isinstance(value, dict) else {}
        return _text_or_null(record.get("message")) or _text_or_null(record.get("error")) or "The export failed."
    return None


def _sanitize_job_error(message: str | None) -> str | None:
    if not message:
        return None
    lower = message.lower()
    if re.search(r"no comments|zero comments", lower):
        return "No publicly accessible comments were available for this source."
    if re.search(r"live chat|not streamed|replay", lower):
        return "Live chat is unavailable because this video was not streamed or has no accessible replay."
    if re.search(r"sign in|advanced mode|cookie|authentication|login", lower):
        return "This export requires authenticated access that the public no-login service does not provide."
    if re.search(r"deleted|not found|private|invalid", lower):
        return "The source appears unavailable, private, deleted, or unsupported in this URL form."
    return "The export failed. The platform may be temporarily unavailable or may not expose this data publicly."


def _guidance(job: JsonRecord, status: str, exported: int | float | None) -> dict[str, Any]:
    raw = (_error_text(job.get("error")) or "").lower()

    def g(code: str, message: str, retryable: bool, action: str) -> dict[str, Any]:
        return {"code": code, "message": message, "retryable": retryable, "suggestedAction": action}

    if status in RUNNING:
        return g("in_progress", "The export is still running. Check status again shortly; do not start a duplicate job for the same URL.", True, "Use action=status with this job ID after a short wait.")
    if status in ("done", "completed") and exported == 0:
        return g("empty_result", "The export completed but returned no usable public records. A completed status does not guarantee that a result file contains data.", False, "Confirm the post is public and has comments, or try a different public URL.")
    if status in ("done", "completed"):
        return g("completed", "The export completed successfully. Requested limits are best-effort and platform batches can be larger.", False, "Use action=data, metadata, or downloads.")
    if re.search(r"no comments|zero comments", raw):
        return g("no_public_data", "No publicly accessible comments were available for this source.", False, "Confirm comments are enabled and the post is public, or use another URL.")
    if re.search(r"live chat|not streamed|replay", raw):
        return g("live_chat_unavailable", "Live chat is unavailable because the video was not streamed or has no accessible replay.", False, "Use comments mode, or provide a streamed video with live-chat replay.")
    if re.search(r"sign in|advanced mode|cookie|authentication|login", raw):
        return g("authentication_required", "This export requires authenticated platform access that the public no-login service does not provide.", False, "Use a public URL or a different supported export mode.")
    if re.search(r"deleted|not found|private|invalid", raw):
        return g("source_unavailable", "The source appears unavailable, private, deleted, or unsupported in this URL form.", False, "Open the URL while signed out, remove share-tracking suffixes, or try the canonical public post URL.")
    if status in ("error", "failed", "cancelled", "stopped"):
        return g("export_failed", "The export failed. The platform may be temporarily unavailable or may not expose this data publicly.", True, "Check capabilities and try once later; repeated failures indicate an unsupported URL or mode.")
    return g("unknown_status", f"The export returned an unfamiliar status: {status}.", True, "Check status again later.")


def _status_of(job: JsonRecord, fallback: str = "") -> str:
    value = job.get("status")
    return (fallback if value is None else js_str(value)).lower()


class ExportService:
    def __init__(self, config: AppConfig, upstream: UpstreamClient | None = None, metadata: MetadataClient | None = None):
        self.config = config
        self.upstream = upstream or UpstreamClient(config)
        self.metadata = metadata or MetadataClient(config.upstream_timeout_ms)
        self._record_cache: dict[str, tuple[float, asyncio.Future[tuple[list[JsonRecord], dict[str, Any]]]]] = {}

    def links(self, job_id: str) -> dict[str, str]:
        return {
            "json": JsUrl(f"/exports/{job_id}.json", self.config.public_base_url).href,
            "csv": JsUrl(f"/exports/{job_id}.csv", self.config.public_base_url).href,
        }

    def to_public_job(self, job: JsonRecord) -> dict[str, Any]:
        job_id = validate_guid("" if job.get("guid") is None else js_str(job.get("guid")))
        status = _status_of(job, "unknown")
        exported = _number_or_null(job.get("total_exported"))
        requested_limit = _number_or_null(_options(job).get("limit"))
        limit_exceeded = None if requested_limit is None or exported is None else exported > requested_limit
        platform = _job_platform(job)
        export_type = _job_mode(job)
        total = _number_or_null(job.get("total"))
        target = [n for n in (requested_limit, total) if n is not None and n > 0]
        if status in RUNNING:
            state = "queued" if status.startswith("queu") else "running"
        elif status in ("done", "completed"):
            state = "completed"
        else:
            state = "failed" if status in TERMINAL else "running"
        support = mode_status(platform, export_type) if platform and export_type else None
        forms = url_form_guidance(platform, export_type) if platform and export_type else []
        if state == "completed":
            progress = 1
        elif state == "running" and exported is not None and target:
            progress = min(0.99, exported / min(target))
        else:
            progress = None
        return {
            "jobId": job_id,
            "status": status,
            "sourceUrl": _text_or_null(job.get("url")),
            "platform": platform,
            "exportType": export_type,
            "state": state,
            "progress": progress,
            "pollAfterSeconds": max(5, js_round(self.config.wait_poll_ms / 1000)) if state in ("queued", "running") else None,
            "totalAvailable": total,
            "recordsExported": exported,
            "repliesExported": _number_or_null(job.get("replies_count")),
            "requestedLimit": requested_limit,
            "limitExceeded": limit_exceeded,
            "resultNotice": f"The platform returned {js_str(exported)} top-level records even though {js_str(requested_limit)} were requested. Limits are best-effort because some platforms return fixed batches." if limit_exceeded else None,
            "error": _sanitize_job_error(_error_text(job.get("error"))),
            "createdAt": _text_or_null(job.get("created_at")),
            "updatedAt": _text_or_null(job.get("updated_at")),
            "downloads": {**self.links(job_id), "available": state == "completed"},
            "guidance": _guidance(job, status, exported),
            "urlGuidance": {
                "confidence": "confirmed" if support == "live_confirmed" else "conditional" if support == "conditional" else "try_it",
                "message": "This mode has completed in live testing, but URL shape and public availability still matter." if support == "live_confirmed" else "This mode is available on a try-it basis because failures can be URL-form-specific.",
                "preferredForms": forms,
                "suggestedAction": "Try one alternate URL form once before concluding the collector is unavailable." if status in ("error", "failed", "stopped") else None,
            } if platform and export_type else None,
        }

    async def start(self, data: StartExportInput) -> dict[str, Any]:
        deadline = _now_ms() + REQUEST_BUDGET_MS
        platform = validate_source_url(data.url)[1]
        detail = PLATFORM_BY_ID.get(platform)
        if not detail:
            raise PublicError("unsupported_url", "This platform is not recognized by the live export service.")
        support = mode_status(platform, data.export_type)
        if support == "not_supported":
            raise PublicError("mode_not_supported", f"{data.export_type} exports are unavailable for {detail['name']}; this mode is hard-disabled because it cannot return the requested public data safely or reliably.")
        if not support:
            raise PublicError("mode_not_supported", f"{data.export_type} is not a recognized export mode for {detail['name']}. Check capabilities for modes and URL forms to try.")
        if data.export_type == "live_chat" and platform not in ("youtube", "twitch", "tiktok"):
            raise PublicError("mode_not_supported", "live_chat exports require a YouTube, Twitch, or TikTok URL.")
        if data.export_type == "facebook_ads" and platform != "facebook":
            raise PublicError("mode_not_supported", "facebook_ads exports require a Facebook page or ads URL.")
        if JsUrl(js_trim(data.url)).hostname.lower() == "redd.it":
            raise PublicError("unsupported_url", "Reddit short links (redd.it) can't be collected. Open the link and use the full thread address: https://www.reddit.com/r/SUBREDDIT/comments/POST_ID/.")
        source = await resolve_source_url(data.url, RESOLVE_BUDGET_MS)
        options: JsonRecord = {}
        if data.limit is not None:
            options["limit"] = data.limit
        if data.advanced_mode:
            options["advanced"] = True
        if data.export_type in ("comments", "reviews", "discussions", "messages"):
            options["replies"] = data.include_replies
        elif data.export_type == "live_chat":
            options["live"] = True
        elif data.export_type == "facebook_ads":
            options["facebookAds"] = True
        elif data.export_type in ("likes", "shares", "followers", "following", "tweets"):
            options[data.export_type] = True
        try:
            job = await self.upstream.create_job(source.normalized, options, deadline - _now_ms())
        except ProviderError as error:
            raise UrlAdjustedProviderError(error, source) from error
        if not isinstance(job, dict):
            job = {}
        requested = {"url": source.normalized, "platform": source.platform, "export_type": data.export_type, "options": {**options, **_options(job)}}
        job = {**job, **requested}
        if data.wait_seconds > 0 and job.get("guid"):
            job = {**(await self.wait_for_job(js_str(job["guid"]), data.wait_seconds, job, deadline)), **requested}
        return {
            **self.to_public_job(job),
            "urlResolution": {
                "originalUrl": source.original,
                "submittedUrl": source.normalized,
                "changed": source.changed,
                "changes": source.changes,
                "redirectChain": source.redirect_chain,
                "resolutionAttempted": source.resolution_attempted,
                "warning": source.resolution_warning,
            },
        }

    async def wait_for_job(self, job_id: str, seconds: int, fallback: JsonRecord | None = None, request_deadline: float | None = None) -> JsonRecord:
        guid = validate_guid(job_id)
        if request_deadline is None:
            request_deadline = _now_ms() + REQUEST_BUDGET_MS
        deadline = min(_now_ms() + seconds * 1000, request_deadline - 2_000)
        job = fallback
        while _now_ms() < deadline:
            try:
                job = await self.upstream.get_job(guid, max(1_000, min(self.config.upstream_timeout_ms, request_deadline - _now_ms() - 1_000)))
            except Exception:
                if job is not None:
                    return job
                raise
            if _status_of(job) in TERMINAL:
                return job
            remaining = deadline - _now_ms()
            if remaining <= 0:
                break
            await asyncio.sleep(min(self.config.wait_poll_ms, remaining) / 1000)
        return job if job is not None else await self.upstream.get_job(guid)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return self.to_public_job(await self.upstream.get_job(validate_guid(job_id)))

    async def _clean(self, job: JsonRecord) -> tuple[list[JsonRecord], dict[str, Any]]:
        raw = await self.upstream.get_records(job)
        threads, thread_summary = normalize_threads(raw, _job_platform(job), _text_or_null(job.get("url")))
        cleaned, quality = clean_records_for_output(threads, [self.config.upstream_base_url.hostname])
        return cleaned, {**quality, **thread_summary}

    async def records_for_completed_job(self, job: JsonRecord) -> tuple[list[JsonRecord], dict[str, Any]]:
        status = _status_of(job)
        if status not in TERMINAL:
            raise PublicError("job_in_progress", "export is still running; check the job and try again", 409, True)
        if status not in ("done", "completed"):
            raise PublicError("export_failed", _sanitize_job_error(_error_text(job.get("error"))) or "export did not complete successfully", 422)
        key = f"{'' if job.get('guid') is None else js_str(job.get('guid'))}|{'' if job.get('json_url') is None else js_str(job.get('json_url'))}"
        now = _now_ms()
        for k in [k for k, (at, _) in self._record_cache.items() if now - at > RECORD_CACHE_TTL_MS]:
            del self._record_cache[k]
        hit = self._record_cache.get(key)
        if hit:
            return await asyncio.shield(hit[1])
        future = asyncio.ensure_future(self._clean(job))
        self._record_cache[key] = (now, future)

        def drop_failed(done: asyncio.Future[Any]) -> None:
            if (done.cancelled() or done.exception()) and self._record_cache.get(key, (0, None))[1] is done:
                del self._record_cache[key]

        future.add_done_callback(drop_failed)
        while len(self._record_cache) > RECORD_CACHE_MAX:
            del self._record_cache[next(iter(self._record_cache))]
        return await asyncio.shield(future)

    async def get_all_records(self, job_id: str) -> list[JsonRecord]:
        return (await self.records_for_completed_job(await self.upstream.get_job(validate_guid(job_id))))[0]

    async def get_records_page(self, job_id: str, page: int, page_size: int) -> dict[str, Any]:
        job = await self.upstream.get_job(validate_guid(job_id))
        public_job = self.to_public_job(job)
        records, quality = await self.records_for_completed_job(job)
        start = (page - 1) * page_size
        empty_result = len(records) == 0
        data_guidance = {
            "code": "empty_result",
            "message": "The result file contains no usable records, even if the job reported an exported count.",
            "retryable": False,
            "suggestedAction": "Confirm the source visibly contains public data, then try one different active public URL or one preferred URL form.",
        } if empty_result else public_job["guidance"]
        limit = public_job["requestedLimit"]
        if empty_result and (public_job["recordsExported"] or 0) > 0:
            result_notice = f"The job reported {js_str(public_job['recordsExported'])} exported record(s), but its result file was empty."
        elif limit is not None and len(records) > limit:
            result_notice = f"The platform returned {len(records)} top-level records even though {js_str(limit)} were requested. Limits are best-effort because some platforms return fixed batches."
        else:
            result_notice = None
        return {
            "records": records[start:start + page_size], "page": page, "pageSize": page_size, "totalRecords": len(records),
            "totalPages": math.ceil(len(records) / page_size), "hasMore": start + page_size < len(records),
            "guidance": data_guidance, "resultNotice": result_notice, "dataQuality": quality,
        }

    async def get_content_metadata(self, job_id: str) -> dict[str, Any]:
        job = await self.upstream.get_job(validate_guid(job_id))
        source_url = _text_or_null(job.get("url"))
        if not source_url:
            raise PublicError("export_failed", "export job does not include a source URL", 422)
        try:
            normalized, platform = validate_source_url(source_url)
        except Exception:
            normalized, platform = source_url, _job_platform(job) or "unknown"
        records: list[JsonRecord] = []
        if _status_of(job) in ("done", "completed"):
            try:
                records = (await self.records_for_completed_job(job))[0]
            except Exception:
                pass
        return await build_content_metadata(normalized, platform, records, self.metadata, rows_are_comments=(_job_mode(job) or "comments") in COMMENT_MODES)

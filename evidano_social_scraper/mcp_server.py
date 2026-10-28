import re
from typing import Any

import mcp_types as types
from mcp.server import Server, ServerRequestContext

from .errors import PublicError
from .js import is_num, js_round, js_str
from .platforms import capabilities_result, url_form_guidance
from .service import ExportService, UrlAdjustedProviderError
from .types import EXPORT_TYPES, StartExportInput
from .upstream import ProviderError
from .validation import validate_source_url

TOOL_NAME = "scrape_social_media"
ACTIONS = ("capabilities", "start", "status", "data", "metadata", "downloads")
INSTRUCTIONS = "Export only publicly accessible social-media data. Do not claim access to private or restricted content. Do not use results for harassment, stalking, biometric identification, sensitive-trait profiling, consequential decisions, spam, or unlawful activity. Explain shared-capacity, rate-limit, best-effort-limit, and unavailable-result guidance to the user."
DESCRIPTION = " ".join([
    "One Evidano tool for the complete social-export workflow.",
    "Use action=capabilities to see live-tested social, review, commerce, community, crowdfunding, gaming, and video collectors.",
    "Use action=start with url and optional export_type/include_replies/limit/wait_seconds/advanced_mode.",
    "Use action=status, data, metadata, or downloads with job_id.",
    "Responses explain pending, empty, failed, authentication-required, rate-limited, and limit-exceeded outcomes and suggest the next action.",
    "Unconfirmed documented modes are tried rather than preemptively rejected; responses include preferred and alternate URL forms.",
    "Before job creation, recognized mobile, legacy, share, short, player, and tracking-heavy URLs are safely normalized; known short links are expanded only through same-platform HTTPS redirects.",
    "The data action accepts page and page_size, cleans common encoding and blank-column artifacts, and reports a dataQuality summary. Metadata returns content/post/video fields separately from comment rows.",
])

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS), "default": "capabilities"},
        "url": {"type": "string", "format": "uri", "description": "Required for start: public URL from a recognized platform. Mobile/share/short variants are normalized automatically when safe."},
        "job_id": {"type": "string", "description": "Required for status, data, metadata, and downloads."},
        "export_type": {"type": "string", "enum": list(EXPORT_TYPES), "default": "comments"},
        "include_replies": {"type": "boolean", "default": True},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100000, "description": "Best-effort maximum; platform batches can exceed it."},
        "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 45, "default": 10},
        "advanced_mode": {"type": "boolean", "default": False, "description": "Ask the processor to use its advanced path; availability varies."},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
    },
    "additionalProperties": False,
    "$schema": "http://json-schema.org/draft-07/schema#",
}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"action": {"type": "string"}, "result": {}},
    "required": ["action"],
    "additionalProperties": False,
    "$schema": "http://json-schema.org/draft-07/schema#",
}
TOOL = types.Tool(
    name=TOOL_NAME,
    title="Scrape social media",
    description=DESCRIPTION,
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    annotations=types.ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)

PUBLIC_ACTIONS = {
    "invalid_request": "Check the URL and the required fields for this action.",
    "unsupported_url": "Use a public URL from a platform listed by action=capabilities.",
    "mode_not_supported": "Choose an export_type listed for this platform by action=capabilities.",
    "job_in_progress": "Check action=status and read the data once the job has completed.",
    "export_failed": "Try one alternate public URL form; repeated failures mean the source or mode is unavailable.",
    "no_public_data": "Confirm the source is public and has comments or reviews, or try another URL.",
    "job_not_found": "Check the job_id, or start the export again.",
}
PROVIDER_CODES = {400: "request_rejected", 401: "service_configuration_error", 403: "service_configuration_error", 404: "job_not_found", 409: "result_unavailable", 422: "request_rejected", 429: "rate_limited"}


class ArgumentError(Exception):
    pass


def _is_int(value: Any) -> bool:
    return is_num(value) and float(value).is_integer()


def parse_arguments(raw: dict[str, Any] | None) -> dict[str, Any]:
    args = raw or {}
    issues: list[str] = []
    out: dict[str, Any] = {}

    def enum(name: str, options: tuple[str, ...], default: str) -> None:
        value = args.get(name, default)
        if value not in options:
            issues.append(f"{name}: Invalid enum value. Expected {' | '.join(repr(o) for o in options)}, received {value!r}")
        out[name] = value

    def boolean(name: str, default: bool) -> None:
        value = args.get(name, default)
        if not isinstance(value, bool):
            issues.append(f"{name}: Expected boolean, received {type(value).__name__}")
        out[name] = value

    def integer(name: str, minimum: int, maximum: int | None, default: int | None) -> None:
        value = args.get(name, default)
        if value is None and default is None and name not in args:
            out[name] = None
            return
        if not _is_int(value):
            issues.append(f"{name}: Expected integer, received {type(value).__name__}")
        elif value < minimum or (maximum is not None and value > maximum):
            issues.append(f"{name}: Number must be {'greater than or equal to ' + str(minimum) if value < minimum else 'less than or equal to ' + str(maximum)}")
        out[name] = int(value) if _is_int(value) else value

    def string(name: str, url: bool = False) -> None:
        value = args.get(name)
        if name not in args:
            out[name] = None
            return
        if not isinstance(value, str):
            issues.append(f"{name}: Expected string, received {type(value).__name__}")
        elif url and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:\S", value.strip()):
            issues.append(f"{name}: Invalid url")
        out[name] = value

    enum("action", ACTIONS, "capabilities")
    string("url", url=True)
    string("job_id")
    enum("export_type", EXPORT_TYPES, "comments")
    boolean("include_replies", True)
    integer("limit", 1, 100000, None)
    integer("wait_seconds", 0, 45, 10)
    boolean("advanced_mode", False)
    integer("page", 1, None, 1)
    integer("page_size", 1, 100, 25)
    if issues:
        raise ArgumentError("; ".join(issues))
    return out


def _result(action: str, value: Any, summary: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=summary)], structured_content={"action": action, "result": value})


def failure(action: str, error: Exception, source_url: str | None = None, export_type: str | None = None) -> types.CallToolResult:
    is_provider = isinstance(error, ProviderError)
    retry = f" Retry after {error.retry_after_seconds} seconds." if is_provider and error.retry_after_seconds else ""
    message = error.message if isinstance(error, (PublicError, ProviderError)) else "The request failed unexpectedly. Try again shortly."
    if isinstance(error, PublicError):
        code = error.code
        suggested_action = PUBLIC_ACTIONS.get(error.code, "Check the request and try again.")
    elif is_provider:
        code = PROVIDER_CODES.get(error.status, "service_error")
        if error.status == 429:
            suggested_action = f"Wait at least {error.retry_after_seconds} seconds, then retry once." if error.retry_after_seconds else "Wait before retrying; do not submit repeated starts."
        elif error.status == 409:
            suggested_action = "Confirm the job is complete and contains public records; otherwise try another public URL."
        elif error.status >= 500:
            suggested_action = "Wait briefly and retry once. If it fails again, try another supported URL or mode."
        else:
            suggested_action = "Check action=capabilities, the URL, required fields, and the selected mode."
    else:
        code = "service_error"
        suggested_action = "Wait briefly and retry once."
    adjusted = error.resolution if isinstance(error, UrlAdjustedProviderError) else None
    url_guidance = None
    if action == "start" and source_url and export_type:
        try:
            platform = validate_source_url(source_url)[1]
            if adjusted:
                attempt = f"Evidano {'normalized the URL before submission' if adjusted.changed else 'checked the URL form before submission'}{'; ' + adjusted.resolution_warning if adjusted.resolution_warning else ''}. The resulting request was still rejected."
            else:
                attempt = "A rejection can be specific to this URL representation."
            url_guidance = {"message": f"{attempt} Try one preferred alternate form before concluding the mode is unavailable.", "preferredForms": url_form_guidance(platform, export_type)}
        except Exception:
            pass
    resolution = {
        "originalUrl": adjusted.original,
        "submittedUrl": adjusted.normalized,
        "changed": adjusted.changed,
        "changes": adjusted.changes,
        "redirectChain": adjusted.redirect_chain,
        "resolutionAttempted": adjusted.resolution_attempted,
        "warning": adjusted.resolution_warning,
    } if adjusted else None
    public_error = {
        "code": code,
        "message": f"{message}{retry}",
        "retryAfterSeconds": error.retry_after_seconds if is_provider else None,
        "suggestedAction": suggested_action,
        "urlGuidance": url_guidance,
        "urlResolution": resolution,
    }
    form_hint = f" {url_guidance['message']} Preferred forms: {'; '.join(url_guidance['preferredForms'])}" if url_guidance else ""
    return types.CallToolResult(
        is_error=True,
        content=[types.TextContent(type="text", text=f"{public_error['message']} {suggested_action}{form_hint}")],
        structured_content={"action": action, "result": {"error": public_error}},
    )


def _job_line(job: dict[str, Any]) -> str:
    counts = ""
    if job["recordsExported"] is not None:
        total = f" of {js_str(job['totalAvailable'])}" if job["totalAvailable"] is not None else ""
        counts = f" {js_str(job['recordsExported'])}{total} records exported."
    progress = f" ({js_round(job['progress'] * 100)}%)" if job["state"] == "running" and job["progress"] is not None else ""
    return f" Job {job['jobId']}: {job['state']}{progress}.{counts}"


def _require_job_id(job_id: str | None) -> str:
    if not job_id:
        raise PublicError("invalid_request", "job_id is required for this action")
    return job_id


async def call_tool(service: ExportService, arguments: dict[str, Any] | None) -> types.CallToolResult:
    try:
        args = parse_arguments(arguments)
    except ArgumentError as error:
        return types.CallToolResult(is_error=True, content=[types.TextContent(type="text", text=f"Input validation error: Invalid arguments for tool {TOOL_NAME}: {error}")])
    action, url, export_type = args["action"], args["url"], args["export_type"]
    try:
        if action == "capabilities":
            return _result(action, capabilities_result(), "Returned platform support, export modes, and additional metadata fields.")
        if action == "start":
            if not url:
                raise PublicError("invalid_request", "url is required for action=start")
            job = await service.start(StartExportInput(
                url=url, export_type=export_type, include_replies=args["include_replies"], limit=args["limit"],
                wait_seconds=args["wait_seconds"], advanced_mode=args["advanced_mode"],
            ))
            return _result(action, {"job": job}, job["guidance"]["message"] + _job_line(job))
        job_id = _require_job_id(args["job_id"])
        if action == "status":
            job = await service.get_job(job_id)
            return _result(action, {"job": job}, job["guidance"]["message"] + _job_line(job))
        if action == "data":
            data = await service.get_records_page(job_id, args["page"], args["page_size"])
            pages = max(1, data["totalPages"])
            return _result(action, data, f"Returned {len(data['records'])} of {data['totalRecords']} records (page {data['page']} of {pages}). {data['resultNotice'] or data['guidance']['message']}")
        if action == "metadata":
            metadata = await service.get_content_metadata(job_id)
            return _result(action, {"metadata": metadata}, f"Returned available {metadata['platform']} content metadata.")
        job = await service.get_job(job_id)
        downloads = job["downloads"]
        summary = f"JSON: {downloads['json']} CSV: {downloads['csv']}" if downloads["available"] else "Downloads are available once the job has completed."
        return _result(action, {"jobId": job["jobId"], "status": job["status"], "state": job["state"], "downloads": downloads, "guidance": job["guidance"], "resultNotice": job["resultNotice"]}, summary + _job_line(job))
    except Exception as error:
        return failure(action, error, url, export_type)


def build_mcp_server(service: ExportService) -> Server:
    async def on_list_tools(ctx: ServerRequestContext, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[TOOL])

    async def on_call_tool(ctx: ServerRequestContext, params: types.CallToolRequestParams) -> types.CallToolResult:
        if params.name != TOOL_NAME:
            return types.CallToolResult(is_error=True, content=[types.TextContent(type="text", text=f"Tool {params.name} not found")])
        return await call_tool(service, params.arguments)

    return Server(
        "Scrape Social Media by Evidano",
        version="1.7.0",
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        get_tool_input_schema=lambda name: INPUT_SCHEMA if name == TOOL_NAME else None,
    )

import asyncio
import re
import time
from dataclasses import dataclass, field

import httpx

from .errors import PublicError
from .js import JsUrl, encode_uri_component, js_trim
from .types import EXPORT_TYPES

PLATFORM_HOSTS: dict[str, str] = dict([
    ("youtube.com", "youtube"), ("www.youtube.com", "youtube"), ("m.youtube.com", "youtube"), ("music.youtube.com", "youtube"), ("youtu.be", "youtube"),
    ("facebook.com", "facebook"), ("www.facebook.com", "facebook"), ("m.facebook.com", "facebook"), ("mbasic.facebook.com", "facebook"), ("fb.watch", "facebook"),
    ("instagram.com", "instagram"), ("www.instagram.com", "instagram"), ("instagr.am", "instagram"), ("tiktok.com", "tiktok"), ("www.tiktok.com", "tiktok"),
    ("m.tiktok.com", "tiktok"), ("vm.tiktok.com", "tiktok"), ("vt.tiktok.com", "tiktok"), ("t.tiktok.com", "tiktok"), ("x.com", "x"),
    ("www.x.com", "x"), ("twitter.com", "x"), ("www.twitter.com", "x"), ("mobile.twitter.com", "x"), ("reddit.com", "reddit"),
    ("www.reddit.com", "reddit"), ("old.reddit.com", "reddit"), ("new.reddit.com", "reddit"), ("redd.it", "reddit"), ("linkedin.com", "linkedin"),
    ("www.linkedin.com", "linkedin"), ("lnkd.in", "linkedin"), ("pinterest.com", "pinterest"), ("www.pinterest.com", "pinterest"), ("api.pinterest.com", "pinterest"),
    ("pin.it", "pinterest"), ("snapchat.com", "snapchat"), ("www.snapchat.com", "snapchat"), ("threads.net", "threads"), ("www.threads.net", "threads"),
    ("bsky.app", "bluesky"), ("twitch.tv", "twitch"), ("www.twitch.tv", "twitch"), ("discord.com", "discord"), ("www.discord.com", "discord"),
    ("discordapp.com", "discord"), ("t.me", "telegram"), ("telegram.me", "telegram"), ("vimeo.com", "vimeo"), ("www.vimeo.com", "vimeo"),
    ("player.vimeo.com", "vimeo"), ("amazon.com", "amazon"), ("www.amazon.com", "amazon"), ("trustpilot.com", "trustpilot"), ("www.trustpilot.com", "trustpilot"),
    ("google.com", "google"), ("www.google.com", "google"), ("maps.google.com", "google"), ("maps.app.goo.gl", "google"), ("goo.gl", "google"),
    ("g.page", "google"), ("search.google.com", "google"), ("etsy.com", "etsy"), ("www.etsy.com", "etsy"), ("steamcommunity.com", "steam"),
    ("store.steampowered.com", "steam"), ("tripadvisor.com", "tripadvisor"), ("www.tripadvisor.com", "tripadvisor"), ("yelp.com", "yelp"), ("www.yelp.com", "yelp"),
    ("airbnb.com", "airbnb"), ("www.airbnb.com", "airbnb"), ("apps.apple.com", "appstore"), ("play.google.com", "google_play"), ("appgallery.huawei.com", "appgallery"),
    ("producthunt.com", "producthunt"), ("www.producthunt.com", "producthunt"), ("kickstarter.com", "kickstarter"), ("www.kickstarter.com", "kickstarter"), ("indiegogo.com", "indiegogo"),
    ("www.indiegogo.com", "indiegogo"), ("walmart.com", "walmart"), ("www.walmart.com", "walmart"), ("bestbuy.com", "bestbuy"), ("www.bestbuy.com", "bestbuy"),
    ("aliexpress.com", "aliexpress"), ("www.aliexpress.com", "aliexpress"), ("flipkart.com", "flipkart"), ("www.flipkart.com", "flipkart"), ("shopee.com", "shopee"),
    ("shopee.sg", "shopee"), ("shopee.com.my", "shopee"), ("shopee.co.id", "shopee"), ("lazada.com", "lazada"), ("www.lazada.com", "lazada"),
    ("lazada.sg", "lazada"), ("www.lazada.sg", "lazada"), ("lazada.com.my", "lazada"), ("www.lazada.com.my", "lazada"), ("lazada.co.id", "lazada"),
    ("www.lazada.co.id", "lazada"), ("lazada.com.ph", "lazada"), ("www.lazada.com.ph", "lazada"), ("imdb.com", "imdb"), ("www.imdb.com", "imdb"),
    ("bilibili.com", "bilibili"), ("www.bilibili.com", "bilibili"), ("m.bilibili.com", "bilibili"), ("kuaishou.com", "kuaishou"), ("www.kuaishou.com", "kuaishou"),
    ("v.kuaishou.com", "kuaishou"), ("chromewebstore.google.com", "chrome_web_store"), ("figma.com", "figma"), ("www.figma.com", "figma"), ("expedia.com", "expedia"),
    ("www.expedia.com", "expedia"), ("vk.com", "vk"), ("www.vk.com", "vk"), ("douyin.com", "douyin"), ("www.douyin.com", "douyin"),
    ("v.douyin.com", "douyin"), ("rutube.ru", "rutube"), ("www.rutube.ru", "rutube"), ("change.org", "changeorg"), ("www.change.org", "changeorg"),
    ("yahoo.com", "yahoo"), ("www.yahoo.com", "yahoo"), ("news.yahoo.com", "yahoo"), ("newsweek.com", "newsweek"), ("www.newsweek.com", "newsweek"),
    ("ebay.com", "ebay"), ("www.ebay.com", "ebay"), ("disqus.com", "disqus"), ("www.disqus.com", "disqus"),
])


def _with_www(domains: list[str]) -> list[str]:
    return [host for d in domains for host in (d, f"www.{d}")]


EXTRA_HOSTS: dict[str, list[str]] = {
    "youtube": _with_www(["youtube-nocookie.com"]),
    "facebook": ["web.facebook.com", "touch.facebook.com"],
    "x": ["mobile.x.com", "m.twitter.com"],
    "reddit": ["m.reddit.com", "sh.reddit.com", "np.reddit.com"],
    "linkedin": [f"{c}.linkedin.com" for c in ["uk", "ca", "au", "in", "ie", "nz", "sg", "de", "fr", "es", "it", "nl", "br", "mx"]],
    "threads": _with_www(["threads.com"]),
    "bluesky": ["www.bsky.app"],
    "twitch": ["m.twitch.tv"],
    "bilibili": ["b23.tv"],
    "chrome_web_store": ["chrome.google.com"],
    "trustpilot": [f"{c}.trustpilot.com" for c in ["uk", "de", "fr", "es", "it", "nl", "dk", "se", "no", "fi", "pl", "pt", "at", "ch", "be", "ie", "au", "ca", "nz", "jp"]],
    "amazon": _with_www(["amazon.co.uk", "amazon.de", "amazon.fr", "amazon.es", "amazon.it", "amazon.nl", "amazon.se", "amazon.pl", "amazon.ca", "amazon.com.mx", "amazon.com.br", "amazon.com.au", "amazon.in", "amazon.co.jp", "amazon.sg", "amazon.ae", "amazon.sa", "amazon.com.tr"]),
    "tripadvisor": _with_www(["tripadvisor.co.uk", "tripadvisor.ca", "tripadvisor.com.au", "tripadvisor.co.nz", "tripadvisor.ie", "tripadvisor.in", "tripadvisor.com.sg", "tripadvisor.de", "tripadvisor.fr", "tripadvisor.es", "tripadvisor.it", "tripadvisor.nl"]),
    "yelp": _with_www(["yelp.co.uk", "yelp.ca", "yelp.com.au", "yelp.ie", "yelp.co.nz", "yelp.de", "yelp.fr", "yelp.es", "yelp.it"]),
    "airbnb": _with_www(["airbnb.co.uk", "airbnb.ca", "airbnb.com.au", "airbnb.co.nz", "airbnb.ie", "airbnb.de", "airbnb.fr", "airbnb.es", "airbnb.it"]),
    "expedia": _with_www(["expedia.co.uk", "expedia.ca", "expedia.com.au", "expedia.de", "expedia.fr"]),
    "ebay": _with_www(["ebay.co.uk", "ebay.de", "ebay.fr", "ebay.it", "ebay.es", "ebay.ca", "ebay.com.au"]),
    "bestbuy": _with_www(["bestbuy.ca"]),
    "walmart": _with_www(["walmart.ca"]),
    "google": _with_www(["google.co.uk", "google.ca", "google.com.au", "google.co.nz", "google.ie", "google.co.in", "google.com.sg", "google.de", "google.fr", "google.es", "google.it"]),
    "lazada": _with_www(["lazada.co.th", "lazada.vn"]),
}
for _platform, _hosts in EXTRA_HOSTS.items():
    for _host in _hosts:
        PLATFORM_HOSTS.setdefault(_host, _platform)


def hosts_for_platform(platform: str) -> list[str]:
    return [host for host, pid in PLATFORM_HOSTS.items() if pid == platform]


CANONICAL_HOSTS = {
    "youtube": "www.youtube.com", "facebook": "www.facebook.com", "instagram": "www.instagram.com", "tiktok": "www.tiktok.com",
    "x": "x.com", "reddit": "www.reddit.com", "linkedin": "www.linkedin.com", "threads": "www.threads.net",
    "twitch": "www.twitch.tv", "producthunt": "www.producthunt.com", "trustpilot": "www.trustpilot.com", "bluesky": "bsky.app",
}
REDIRECT_HOSTS = {
    "fb.watch", "instagr.am", "vm.tiktok.com", "vt.tiktok.com", "t.tiktok.com", "redd.it", "lnkd.in", "pin.it", "api.pinterest.com",
    "maps.app.goo.gl", "goo.gl", "g.page", "v.kuaishou.com", "v.douyin.com", "b23.tv",
}
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "igsh", "igshid", "mibextid", "ref_src", "ref_url", "si", "feature", "pp", "app",
    "ab_channel", "extid", "share_url", "invite_code", "sender", "sender_device", "sender_web_id", "sfo", "source", "skid",
    "g_ep", "authuser", "entry", "ved", "usg", "trk", "trackingid", "lipi", "midtoken", "is_from_webapp", "share_app_id",
    "share_iid", "share_link_id", "share_id", "rdt", "social_share_type", "timestamp", "ttclid", "_ttp", "_r", "_t",
}


@dataclass
class UrlResolution:
    original: str
    normalized: str
    platform: str
    changed: bool
    changes: list[str]
    redirect_chain: list[str] = field(default_factory=list)
    resolution_attempted: bool = False
    resolution_warning: str | None = None


def _parse_recognized_url(raw: str) -> tuple[JsUrl, str]:
    try:
        url = JsUrl(js_trim(raw))
    except ValueError:
        raise PublicError("invalid_request", "source URL is invalid") from None
    if url.protocol != "https:":
        raise PublicError("invalid_request", "source URL must use HTTPS")
    if url.username or url.password:
        raise PublicError("invalid_request", "source URL must not contain credentials")
    if url.port:
        raise PublicError("unsupported_url", "source URL must not name a port")
    host = url.hostname.lower()
    platform = PLATFORM_HOSTS.get(host[:-1] if host.endswith(".") else host)
    if not platform:
        raise PublicError("unsupported_url", "source URL is not from a recognized social platform")
    return url, platform


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _drop_tracking(url: JsUrl, platform: str, changes: list[str]) -> None:
    removed: list[str] = []
    for key in url.param_keys():
        lower = key.lower()
        platform_tracking = (platform == "tiktok" and (lower.startswith("tt_") or lower.startswith("share_"))) \
            or (platform == "x" and lower in ("s", "t"))
        share_ref = lower == "ref" and (url.param_get(key) or "").lower() in ("share", "sharing")
        if lower.startswith("utm_") or lower in TRACKING_PARAMS or platform_tracking or share_ref:
            url.param_delete(key)
            removed.append(key)
    if removed:
        changes.append(f"removed tracking parameters: {', '.join(_unique(removed))}")


def _canonicalize(url: JsUrl, platform: str, changes: list[str]) -> JsUrl:
    before_host = url.hostname
    if platform == "youtube":
        short_id = next((s for s in url.pathname.split("/") if s), None) if url.hostname == "youtu.be" else None
        path_match = re.match(r"^/(?:shorts|embed|live)/([^/]+)", url.pathname)
        path_id = path_match.group(1) if path_match else None
        if short_id or path_id:
            url.hostname = "www.youtube.com"
            url.pathname = "/watch"
            url.search = ""
            url.param_set("v", short_id or path_id)
            changes.append("converted a YouTube share/player URL to its watch URL")
        else:
            if url.hostname != "www.youtube.com":
                url.hostname = "www.youtube.com"
            if url.pathname == "/watch" and url.param_get("v"):
                extra = [key for key in url.param_keys() if key != "v"]
                if extra:
                    url.search = f"?v={encode_uri_component(url.param_get('v'))}"
                    changes.append(f"removed YouTube playback parameters: {', '.join(_unique(extra))}")
    elif platform == "reddit" and url.hostname == "redd.it":
        pass
    elif platform == "x":
        url.hostname = "x.com"
        web = re.match(r"^/i/web/status/(\d+)", url.pathname)
        if web:
            url.pathname = f"/i/status/{web.group(1)}"
            changes.append("converted an X web-client link to its post URL")
        status = re.match(r"^(/[^/]+/status/\d+)(?:/(?:photo|video)/\d+)?/?$", url.pathname)
        if status and status.group(1) != url.pathname:
            url.pathname = status.group(1)
            changes.append("removed an X media-view suffix from the post URL")
    elif platform == "pinterest":
        pin = re.match(r"^/pin/(\d+)(?:/sent)?/?$", url.pathname)
        if pin:
            url.hostname = "www.pinterest.com"
            url.pathname = f"/pin/{pin.group(1)}/"
            changes.append("converted a Pinterest sent/share URL to its pin URL")
    elif platform == "vimeo" and re.match(r"^/(?:channels/[^/]+|video)/(\d+)", url.pathname) and url.hostname in ("vimeo.com", "www.vimeo.com", "player.vimeo.com"):
        url.pathname = "/" + re.search(r"/(\d+)", url.pathname).group(1)
        url.hostname = "vimeo.com"
        url.search = ""
        changes.append("converted a Vimeo channel/player URL to its video URL")
    elif platform == "chrome_web_store" and url.hostname == "chrome.google.com" and url.pathname.startswith("/webstore/detail/"):
        url.hostname = "chromewebstore.google.com"
        url.pathname = re.sub(r"^/webstore", "", url.pathname)
        changes.append("converted a legacy Chrome Web Store URL to its current form")
    elif CANONICAL_HOSTS.get(platform) and url.hostname not in REDIRECT_HOSTS:
        url.hostname = CANONICAL_HOSTS[platform]

    if url.hostname != before_host and not any("share/player" in c or "Reddit short" in c for c in changes):
        changes.append(f"normalized mobile/legacy host {before_host} to {url.hostname}")
    _drop_tracking(url, platform, changes)
    if url.hash:
        url.hash = ""
        changes.append("removed the URL fragment")
    return url


def validate_source_url(raw: str) -> tuple[str, str]:
    url, platform = _parse_recognized_url(raw)
    return _canonicalize(url, platform, []).href, platform


def _needs_redirect_resolution(url: JsUrl) -> bool:
    if url.hostname in REDIRECT_HOSTS:
        return url.hostname != "redd.it"
    if url.hostname.endswith("reddit.com") and re.match(r"^/r/[^/]+/s/[^/]+", url.pathname):
        return True
    if url.hostname.endswith("facebook.com") and url.pathname.startswith("/share/"):
        return True
    if url.hostname.endswith("instagram.com") and url.pathname.startswith("/share/"):
        return True
    return url.hostname.endswith("tiktok.com") and url.pathname.startswith("/t/")


def _has_usable_resolved_path(url: JsUrl, platform: str) -> bool:
    if platform == "tiktok":
        return bool(re.match(r"^/@[^/]+/(?:video|photo)/\d+", url.pathname))
    if platform == "facebook":
        return url.pathname != "/" and not url.pathname.startswith("/login")
    if platform == "instagram":
        return bool(re.match(r"^/(?:p|reel|tv)/[^/]+", url.pathname))
    if platform == "google":
        return url.hostname == "www.google.com" and url.pathname.startswith("/maps/")
    if platform == "linkedin":
        return url.hostname.endswith("linkedin.com") and url.pathname != "/"
    if platform == "reddit":
        return bool(re.match(r"^/r/[^/]+/comments/[^/]+", url.pathname))
    return url.pathname != "/"


async def _fetch_redirect(url: JsUrl, timeout_ms: float) -> tuple[int, str | None]:
    async with asyncio.timeout(timeout_ms / 1000):
        async with httpx.AsyncClient(follow_redirects=False, timeout=None) as client:
            async with client.stream("GET", url.href, headers={"accept": "text/html,application/xhtml+xml", "user-agent": "Evidano-URL-Resolver/1.0"}) as response:
                return response.status_code, response.headers.get("location")


def _now_ms() -> float:
    return time.monotonic() * 1000


async def resolve_source_url(raw: str, budget_ms: int = 8_000) -> UrlResolution:
    deadline = _now_ms() + budget_ms
    original = js_trim(raw)
    parsed_url, original_platform = _parse_recognized_url(original)
    changes: list[str] = []
    redirect_chain: list[str] = []
    current = parsed_url
    resolution_attempted = _needs_redirect_resolution(current)
    resolution_warning: str | None = None

    if resolution_attempted:
        try:
            for _ in range(5):
                remaining = deadline - _now_ms()
                if remaining < 250:
                    break
                status, location = await _fetch_redirect(current, remaining)
                if not location or status < 300 or status >= 400:
                    break
                next_url, next_platform = _parse_recognized_url(JsUrl(location, current).href)
                if next_platform != original_platform:
                    raise RuntimeError("share URL redirected outside its original platform")
                current = next_url
                redirect_chain.append(current.href)
                if not _needs_redirect_resolution(current):
                    break
            if not redirect_chain or not _has_usable_resolved_path(current, original_platform):
                resolution_warning = "The share URL did not resolve to a usable public content URL; Evidano will still try the supplied platform URL."
                current = parsed_url
                redirect_chain.clear()
            else:
                changes.append(f"resolved {len(redirect_chain)} verified platform redirect{'' if len(redirect_chain) == 1 else 's'}")
        except Exception:
            resolution_warning = "The share URL could not be expanded safely; Evidano will still try the supplied platform URL."
            current = parsed_url
            redirect_chain.clear()

    normalized = _canonicalize(current, original_platform, changes).href
    return UrlResolution(
        original=original, normalized=normalized, platform=original_platform, changed=normalized != original,
        changes=_unique(changes), redirect_chain=redirect_chain, resolution_attempted=resolution_attempted,
        resolution_warning=resolution_warning,
    )


def validate_export_type(value: str) -> str:
    if value not in EXPORT_TYPES:
        raise PublicError("invalid_request", f"export type must be one of: {', '.join(EXPORT_TYPES)}")
    return value


def validate_guid(value: str) -> str:
    guid = js_trim(value).lower()
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", guid):
        raise PublicError("invalid_request", "jobId must be a valid UUID")
    return guid


def assert_safe_result_url(raw: str, upstream_base_url: JsUrl) -> JsUrl:
    url = JsUrl(raw)
    if url.protocol != "https:" or url.origin != upstream_base_url.origin or not url.pathname.startswith("/exports/"):
        raise RuntimeError("provider returned an unsafe result URL")
    return url

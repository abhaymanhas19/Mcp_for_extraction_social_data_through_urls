import re
from typing import Any

from .validation import hosts_for_platform

PLATFORM_DETAILS: list[dict[str, Any]] = [
    {"id": "youtube", "name": "YouTube", "support": "live_confirmed", "exportTypes": ["comments", "live_chat"], "additionalData": ["video title", "video ID", "channel name and URL", "thumbnail", "comment count"], "notes": ["Standard videos and Shorts completed in live tests.", "Title/channel/thumbnail use public oEmbed.", "Live chat is accepted only for a stream with an available replay.", "Live audit (Oct 2026): two videos and a past livestream's chat replay completed; comments the result file repeats are removed."]},
    {"id": "facebook", "name": "Facebook", "support": "live_confirmed", "exportTypes": ["comments", "posts", "videos"], "additionalData": ["post ID and URL", "page/comment author", "post/comment text", "reactions", "mentions", "media", "video details", "timestamps"], "notes": ["Post comments, public page posts, and a public page reels/video export completed in live tests.", "Two event-tab tests returned ordinary post-shaped rows, so events are hard-disabled; shares and followers remain authentication-dependent trial modes.", "Live audit (Oct 2026): public page posts completed; a page reels URL failed."]},
    {"id": "instagram", "name": "Instagram", "support": "live_confirmed", "exportTypes": ["comments", "posts"], "additionalData": ["caption", "shortcode and media ID", "owner/profile", "media type", "thumbnail/display URL", "duration", "views", "location"], "notes": ["Reel/photo comments and two public profile-post exports completed.", "Likes and followers failed; hashtag search requested authentication. Those modes are disabled."]},
    {"id": "tiktok", "name": "TikTok", "support": "live_confirmed", "exportTypes": ["comments", "search", "followers", "videos"], "additionalData": ["video caption/title", "video ID and URL", "creator name and URL", "thumbnail", "play/comment/share counts", "profile and follower fields"], "notes": ["Comments, search-result videos, profile followers, and profile videos are live-confirmed.", "Profile videos were initially empty for several fixtures, then two retries returned 100 correctly shaped rows each; treat an empty completion as retryable once with another active public profile.", "Likes are hard-disabled after returning comment-shaped rows."]},
    {"id": "x", "name": "X", "support": "live_confirmed", "exportTypes": ["comments", "followers", "following", "tweets", "search"], "additionalData": ["post text and ID", "author/profile", "media", "likes", "reposts", "replies", "account counts", "location and bio"], "notes": ["Comments, followers, following, profile tweets, and search completed in live tests.", "Likes returned comment-shaped rows and are disabled.", "Profile-scale exports can exceed requested limits because results are batched."]},
    {"id": "reddit", "name": "Reddit", "support": "live_confirmed", "exportTypes": ["comments", "posts"], "additionalData": ["post title", "post and comment IDs/URLs", "subreddit", "author", "score and votes", "thread depth"], "notes": ["Thread comments, user comments, and user submissions completed in live tests.", "Private Reddit chat is excluded because the public service does not collect user credentials.", "Live audit (Oct 2026): two threads and a user's submissions completed; redd.it short links failed both rewritten to /comments/ID and as given, so they are refused with the full /r/SUBREDDIT/comments/ID/ form to use."]},
    {"id": "linkedin", "name": "LinkedIn", "support": "conditional", "exportTypes": ["comments"], "additionalData": ["post/comment URL", "author/profile/headline", "reactions", "likes", "comments", "shares", "impressions"], "notes": ["Post comments are confirmed; a limit-3 test returned 50 rows.", "Company posts/people failed and search required authentication, so those modes are disabled.", "Live audit (Oct 2026): two public post URLs asked for sign-in; comments are conditional."]},
    {"id": "pinterest", "name": "Pinterest", "support": "not_supported", "exportTypes": [], "additionalData": ["pin title", "description", "board", "creator", "image", "saves"], "notes": ["Live API rejected a public pin URL.", "Live audit (Oct 2026): a public pin URL was rejected before collection."]},
    {"id": "snapchat", "name": "Snapchat", "support": "not_supported", "exportTypes": [], "additionalData": ["Spotlight caption", "creator", "media", "views and engagement"], "notes": ["Live API rejected a public Spotlight URL.", "Live audit (Oct 2026): two Spotlight URLs were rejected before collection."]},
    {"id": "threads", "name": "Threads", "support": "live_confirmed", "exportTypes": ["posts", "comments"], "additionalData": ["post text", "author", "media", "likes and replies"], "notes": ["Five public profile-post exports completed in the 120-URL audit; individual post comments remain URL-dependent.", "Live audit (Oct 2026): a public post's replies completed; threads.com links are accepted and submitted in the threads.net form."]},
    {"id": "bluesky", "name": "Bluesky", "support": "live_confirmed", "exportTypes": ["comments", "posts"], "additionalData": ["post text and permalink", "author/handle/avatar", "language", "likes", "replies", "reposts", "quotes", "bookmarks"], "notes": ["Post comments and public profile posts completed in live tests."]},
    {"id": "twitch", "name": "Twitch", "support": "conditional", "exportTypes": ["videos"], "additionalData": ["live chat messages", "channel/video context", "author and timestamp"], "notes": ["User-video collection is conditional: one active channel failed and two jobs reported an exported item but supplied an empty JSON array.", "Try another active public channel once; do not treat a reported count as usable until action=data returns rows.", "Live audit (Oct 2026): a channel's videos and a VOD's chat both completed without rows."]},
    {"id": "discord", "name": "Discord", "support": "not_supported", "exportTypes": [], "additionalData": ["message text", "author", "timestamp", "attachments", "channel/thread context"], "notes": ["Public-message testing failed and the current collector requires authentication context the service does not collect."]},
    {"id": "telegram", "name": "Telegram", "support": "not_supported", "exportTypes": [], "additionalData": ["public channel post text", "author/channel", "media", "views and reactions"], "notes": ["Live API rejected a public channel-post URL.", "Live audit (Oct 2026): two public channel-post URLs were rejected before collection."]},
    {"id": "vimeo", "name": "Vimeo", "support": "live_confirmed", "exportTypes": ["comments"], "additionalData": ["video ID", "comment author/time", "engagement fields"], "notes": ["Public oEmbed was unavailable for the tested video, so title enrichment is best-effort."]},
    {"id": "producthunt", "name": "Product Hunt", "support": "not_supported", "exportTypes": [], "additionalData": ["author and username", "date", "comment or review text", "rating", "pros and cons", "maker status"], "notes": ["Both a product reviews URL and a product comments URL failed in live tests."]},
    {"id": "changeorg", "name": "Change.org", "support": "not_supported", "exportTypes": [], "additionalData": ["name", "date", "likes", "comment", "location"], "notes": ["The tested public petition returned no public comment data."]},
    {"id": "figma", "name": "Figma Community", "support": "not_supported", "exportTypes": [], "additionalData": ["comment ID", "text", "author", "date", "likes"], "notes": ["Two public community-plugin URLs failed; design-file comments require a personal token and are out of scope."]},
    {"id": "indiegogo", "name": "Indiegogo", "support": "not_supported", "exportTypes": [], "additionalData": ["author", "badge", "votes", "date", "comment"], "notes": ["The tested public campaign discussion was unavailable in the accepted URL form."]},
    {"id": "kickstarter", "name": "Kickstarter", "support": "live_confirmed", "exportTypes": ["comments"], "additionalData": ["author", "badge", "date", "comment"], "notes": ["Two large public project comment pages completed; collection returned 25-row batches despite a limit of one."]},
    {"id": "google", "name": "Google Maps and Search", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["place name", "rating", "review count", "address", "phone", "website", "review text"], "notes": ["A current Google Maps mobile share link was expanded automatically to its full single-place URL and completed with correctly shaped review data.", "Place-search exports remain URL-dependent and are available on a try-it basis.", "Live audit (Oct 2026): place_id and ?cid= links failed; use a maps.app.goo.gl share link or a full /maps/place/ URL."]},
    {"id": "etsy", "name": "Etsy", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["transaction ID", "author", "date", "rating", "review", "product and shop"], "notes": ["Two product listings completed with review-shaped schemas; requested limits may be exceeded."]},
    {"id": "flipkart", "name": "Flipkart", "support": "not_supported", "exportTypes": [], "additionalData": ["author", "date", "rating", "review", "product ID", "brand", "price"], "notes": ["Search failed and a public review job remained non-terminal beyond the bounded audit window."]},
    {"id": "tripadvisor", "name": "Tripadvisor", "support": "conditional", "exportTypes": ["reviews"], "additionalData": ["author", "date", "rating", "review", "traveler type", "location"], "notes": ["A public hotel page completed with a review-shaped schema.", "Live audit (Oct 2026): a restaurant page and a hotel page both failed; reviews are conditional."]},
    {"id": "bestbuy", "name": "Best Buy", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["author", "date", "rating", "review", "verified purchase", "helpful votes"], "notes": ["Two product review pages completed with matching schemas; collection returned 20-row batches despite a limit of one."]},
    {"id": "trustpilot", "name": "Trustpilot", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["author", "country", "verified status", "date", "rating", "review", "company reply"], "notes": ["Two company review pages completed with matching review schemas."]},
    {"id": "expedia", "name": "Expedia", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["author", "date", "rating", "review", "trip details"], "notes": ["A public hotel page completed with 50 review rows despite a limit of one.", "Live audit (Oct 2026): the full hotel-information URL completed; the short h<ID>.Hotel-Information form was rejected."]},
    {"id": "yelp", "name": "Yelp", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["author", "date", "rating", "review", "useful/funny/cool", "business details"], "notes": ["One business review URL completed; another became unavailable. Restaurant search/listings failed and are disabled."]},
    {"id": "walmart", "name": "Walmart", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["author", "date", "rating", "review", "helpful votes"], "notes": ["Five dedicated /reviews/product/{id} URLs completed with review-shaped data; the five matching /ip/{id} forms were rejected before job creation."]},
    {"id": "appstore", "name": "Apple App Store", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["author", "date", "rating", "title", "review"], "notes": ["Two app pages completed with matching review schemas; collection returned 20-row batches despite a limit of one."]},
    {"id": "google_play", "name": "Google Play Store", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["author", "date", "rating", "review", "helpful count", "language"], "notes": ["Two app pages completed and honored a limit of one."]},
    {"id": "airbnb", "name": "Airbnb", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["author", "date", "rating", "review", "listing context"], "notes": ["Three public listing pages completed with matching review schemas; limits were exceeded."]},
    {"id": "aliexpress", "name": "AliExpress", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["author", "date", "rating", "review", "variant", "country"], "notes": ["No stable public product fixture could be validated in the bounded audit; not exposed until a successful live result exists.", "Live audit (Oct 2026): one of two item pages completed with review rows."]},
    {"id": "amazon", "name": "Amazon", "support": "not_supported", "exportTypes": [], "additionalData": ["author", "date", "rating", "title", "review", "verified purchase"], "notes": ["Two public product URLs failed after job creation, including one that reported available reviews but delivered no usable export.", "Live audit (Oct 2026): two product URLs and a slug/ref form failed after collection started."]},
    {"id": "lazada", "name": "Lazada", "support": "not_supported", "exportTypes": [], "additionalData": ["author", "date", "rating", "review", "product or shop context"], "notes": ["No stable product or shop review fixture completed in the bounded audit; not exposed until verified."]},
    {"id": "chrome_web_store", "name": "Chrome Web Store", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["author", "date", "rating", "review", "helpful count", "developer reply"], "notes": ["A public extension with substantial ratings completed with a matching review schema; an extension with only two ratings failed."]},
    {"id": "steam", "name": "Steam", "support": "live_confirmed", "exportTypes": ["reviews", "discussions"], "additionalData": ["author", "date", "comment or review", "recommendation", "playtime", "helpfulness", "purchase type"], "notes": ["App reviews and three exact discussion-thread URLs completed with correctly shaped rows.", "Use an individual /discussions/... thread; app-level discussion indexes and several group-comment pages failed."]},
    {"id": "appgallery", "name": "Huawei AppGallery", "support": "live_confirmed", "exportTypes": ["reviews"], "additionalData": ["author", "date", "rating", "review", "app version"], "notes": ["A public app URL failed in live testing.", "Live audit (Oct 2026): an app page completed with review rows."]},
    {"id": "imdb", "name": "IMDb", "support": "not_supported", "exportTypes": [], "additionalData": ["author", "date", "rating", "title", "review", "helpfulness"], "notes": ["Two well-known movie review pages failed in live tests.", "Live audit (Oct 2026): two title review pages failed."]},
    {"id": "vk", "name": "VK", "support": "live_confirmed", "exportTypes": ["comments"], "additionalData": ["author", "date", "likes", "comment or post text", "media"], "notes": ["No stable public fixture could be verified in the bounded audit; not exposed until a successful result exists.", "Live audit (Oct 2026): a public wall post's comments completed; a community page was rejected for posts."]},
    {"id": "yahoo", "name": "Yahoo", "support": "not_supported", "exportTypes": [], "additionalData": ["name", "date", "upvotes", "downvotes", "comment", "replies"], "notes": ["The tested public article URL was unavailable to the collector."]},
    {"id": "newsweek", "name": "Newsweek", "support": "not_supported", "exportTypes": [], "additionalData": ["name", "username", "date", "votes", "comment", "replies"], "notes": ["The tested public article URL failed in live testing."]},
    {"id": "kuaishou", "name": "Kuaishou", "support": "live_confirmed", "exportTypes": ["comments"], "additionalData": ["author name and ID", "date", "likes", "comment", "media ID"], "notes": ["No stable public fixture could be verified; not exposed until a successful result exists.", "Live audit (Oct 2026): one of two short-video URLs completed with comment rows."]},
    {"id": "bilibili", "name": "Bilibili", "support": "live_confirmed", "exportTypes": ["comments"], "additionalData": ["author name and ID", "date", "likes", "comment", "reply count"], "notes": ["Two public video pages completed with matching comment schemas; collection returned 20-row batches despite a limit of one."]},
    {"id": "rutube", "name": "RuTube", "support": "live_confirmed", "exportTypes": ["comments"], "additionalData": ["author name and ID", "date", "likes", "comment", "video ID"], "notes": ["Two public video pages completed and honored a limit of one."]},
    {"id": "douyin", "name": "Douyin", "support": "live_confirmed", "exportTypes": ["comments"], "additionalData": ["author", "date", "likes", "comment", "video ID"], "notes": ["No stable public fixture could be verified; not exposed until a successful result exists.", "Live audit (Oct 2026): two video URLs completed with comment rows."]},
    {"id": "ebay", "name": "eBay", "support": "not_supported", "exportTypes": [], "additionalData": ["author", "date", "rating", "review", "item context"], "notes": ["Not yet verified in live testing.", "Live audit (Oct 2026): an item URL was rejected before collection."]},
    {"id": "disqus", "name": "Disqus", "support": "try_supported", "exportTypes": ["comments"], "additionalData": ["username", "name", "date", "comment", "votes", "replies", "thread context"], "notes": ["Disqus comments are collected from canonical disqus.com discussion URLs; comment threads embedded on other sites are not accepted by this public service.", "Live audit (Oct 2026): two disqus.com discussion URLs were rejected before collection."]},
    {"id": "shopee", "name": "Shopee", "support": "not_supported", "exportTypes": [], "additionalData": ["author", "date", "rating", "review", "product or shop context"], "notes": ["Product and shop review collection is currently unavailable."]},
]

PLATFORM_BY_ID = {item["id"]: item for item in PLATFORM_DETAILS}

HARD_DISABLED_MODES = {
    "facebook:likes", "facebook:events", "tiktok:likes", "x:likes", "reddit:messages", "discord:comments", "discord:messages", "linkedin:members", "shopee:reviews",
}

URL_FORM_GUIDANCE: dict[str, list[str]] = {
    "youtube": ["https://www.youtube.com/watch?v=VIDEO_ID", "https://youtu.be/VIDEO_ID", "https://www.youtube.com/shorts/VIDEO_ID"],
    "facebook": ["Canonical public post, reel, page, profile, group, or feature-tab URL", "If a share/mobile URL fails, open it signed out and copy the final www.facebook.com URL"],
    "instagram": ["https://www.instagram.com/p/SHORTCODE/", "https://www.instagram.com/reel/SHORTCODE/", "https://www.instagram.com/USERNAME/"],
    "tiktok": ["https://www.tiktok.com/@USER/video/VIDEO_ID", "https://www.tiktok.com/@USER", "Share links such as https://vm.tiktok.com/... may work; try the resolved canonical URL if they fail"],
    "x": ["https://x.com/USER/status/POST_ID", "https://twitter.com/USER/status/POST_ID", "https://x.com/USER"],
    "reddit": ["https://www.reddit.com/r/SUBREDDIT/comments/POST_ID/...", "https://www.reddit.com/user/USERNAME/", "old.reddit.com equivalents may work"],
    "google": ["Full https://www.google.com/maps/place/.../@LAT,LONG,... URL", "https://www.google.com/maps?cid=NUMERIC_CID", "Google Maps Share link: https://maps.app.goo.gl/... or https://goo.gl/maps/...", "For place search use https://www.google.com/maps/search/QUERY; for reviews prefer a single-place URL"],
    "amazon": ["https://www.amazon.com/dp/ASIN", "https://www.amazon.com/product-reviews/ASIN", "Remove ref, tag, and tracking query parameters if a URL fails"],
    "walmart": ["https://www.walmart.com/ip/PRODUCT/ITEM_ID", "https://www.walmart.com/reviews/product/ITEM_ID"],
    "producthunt": ["https://www.producthunt.com/products/PRODUCT", "A launch URL may behave differently from the product hub; try the product hub for reviews"],
    "linkedin": ["Canonical public linkedin.com/posts/... URL", "Public profile/company/group feed URL for posts", "Search and people pages may require authenticated access"],
    "threads": ["https://www.threads.net/@USER/post/POST_ID", "https://threads.net/@USER/post/POST_ID", "https://www.threads.net/@USER for user posts"],
    "steam": ["https://store.steampowered.com/app/APP_ID/... for reviews", "https://steamcommunity.com/app/APP_ID/discussions/... for discussions", "https://steamcommunity.com/id/USER or /profiles/ID for profile comments"],
    "disqus": ["Use a canonical public discussion URL on disqus.com", "Embedded Disqus threads on arbitrary publisher domains are intentionally not allowlisted by this public service"],
}

MODE_URL_FORM_GUIDANCE: dict[str, list[str]] = {
    "google:reviews": ["https://maps.app.goo.gl/... share URL (expanded automatically; live-confirmed)", "Full https://www.google.com/maps/place/... URL containing the place data token", "https://www.google.com/maps?cid=NUMERIC_CID"],
    "google:search": ["https://www.google.com/maps/search/QUERY", "https://www.google.com/maps/search/?api=1&query=ENCODED_QUERY"],
    "walmart:reviews": ["https://www.walmart.com/reviews/product/ITEM_ID (live-confirmed)", "A matching https://www.walmart.com/ip/.../ITEM_ID page may be rejected; switch to the dedicated reviews URL"],
    "threads:posts": ["https://www.threads.net/@USER (live-confirmed)", "https://threads.net/@USER"],
    "threads:comments": ["https://www.threads.net/@USER/post/POST_ID", "https://threads.net/@USER/post/POST_ID"],
    "steam:reviews": ["https://store.steampowered.com/app/APP_ID/ (live-confirmed)"],
    "steam:discussions": ["Use a specific thread URL under https://steamcommunity.com/app/APP_ID/discussions/...", "An app-level discussions index may fail because it is not one conversation thread"],
    "producthunt:reviews": ["https://www.producthunt.com/products/PRODUCT/reviews", "If it has no public review data, try the canonical product hub or another product"],
    "producthunt:comments": ["Use a specific Product Hunt launch/post URL when possible", "A /products/PRODUCT hub is not necessarily a comment thread"],
    "linkedin:posts": ["https://www.linkedin.com/company/COMPANY/posts/", "https://www.linkedin.com/in/PROFILE/recent-activity/all/"],
    "linkedin:members": ["https://www.linkedin.com/company/COMPANY/people/; this may require authenticated platform access"],
    "facebook:posts": ["https://www.facebook.com/PAGE/posts", "https://www.facebook.com/PAGE/community", "If a tab URL fails, try the canonical page/profile URL"],
    "facebook:reviews": ["https://www.facebook.com/PAGE/reviews; availability depends on whether public recommendations are enabled"],
    "facebook:videos": ["https://www.facebook.com/PAGE/reels", "Use the canonical reel/video permalink when collecting comments from one item"],
}

MODE_SUPPORT: dict[str, dict[str, str]] = {
    "youtube": {"comments": "live_confirmed", "videos": "try_supported", "likes": "not_supported", "live_chat": "live_confirmed"},
    "facebook": {"comments": "live_confirmed", "posts": "live_confirmed", "likes": "not_supported", "shares": "not_supported", "facebook_ads": "not_supported", "videos": "live_confirmed", "events": "not_supported", "mentions": "not_supported", "followers": "not_supported", "following": "not_supported", "members": "not_supported", "reviews": "not_supported"},
    "instagram": {"comments": "live_confirmed", "posts": "live_confirmed", "likes": "not_supported", "followers": "not_supported", "following": "not_supported", "search": "not_supported"},
    "tiktok": {"comments": "live_confirmed", "search": "live_confirmed", "likes": "not_supported", "shares": "not_supported", "followers": "live_confirmed", "following": "not_supported", "videos": "live_confirmed", "live_chat": "not_supported"},
    "x": {"comments": "live_confirmed", "followers": "live_confirmed", "following": "live_confirmed", "tweets": "live_confirmed", "search": "live_confirmed", "likes": "not_supported"},
    "reddit": {"comments": "live_confirmed", "posts": "live_confirmed", "messages": "not_supported"},
    "linkedin": {"comments": "conditional", "likes": "not_supported", "shares": "not_supported", "posts": "not_supported", "members": "not_supported", "search": "not_supported"},
    "pinterest": {"comments": "try_supported", "posts": "try_supported"},
    "snapchat": {"comments": "try_supported", "videos": "try_supported"},
    "threads": {"comments": "live_confirmed", "likes": "not_supported", "posts": "live_confirmed"},
    "bluesky": {"comments": "live_confirmed", "posts": "live_confirmed"},
    "twitch": {"comments": "not_supported", "live_chat": "not_supported", "videos": "conditional"},
    "discord": {"comments": "not_supported", "messages": "not_supported"},
    "telegram": {"comments": "try_supported", "posts": "try_supported"},
    "vimeo": {"comments": "live_confirmed"},
    "producthunt": {"comments": "not_supported", "reviews": "not_supported"},
    "changeorg": {"comments": "not_supported"},
    "figma": {"comments": "not_supported"},
    "indiegogo": {"comments": "not_supported", "discussions": "not_supported"},
    "kickstarter": {"comments": "live_confirmed"},
    "google": {"reviews": "live_confirmed", "search": "try_supported"},
    "etsy": {"reviews": "live_confirmed"},
    "flipkart": {"reviews": "not_supported", "search": "not_supported"},
    "tripadvisor": {"reviews": "conditional"},
    "bestbuy": {"reviews": "live_confirmed"},
    "trustpilot": {"reviews": "live_confirmed"},
    "expedia": {"reviews": "live_confirmed"},
    "yelp": {"reviews": "live_confirmed"},
    "walmart": {"reviews": "live_confirmed"},
    "appstore": {"reviews": "live_confirmed"},
    "google_play": {"reviews": "live_confirmed"},
    "airbnb": {"reviews": "live_confirmed"},
    "aliexpress": {"reviews": "live_confirmed"},
    "amazon": {"reviews": "not_supported"},
    "lazada": {"reviews": "not_supported"},
    "shopee": {"reviews": "not_supported"},
    "chrome_web_store": {"reviews": "live_confirmed"},
    "steam": {"reviews": "live_confirmed", "discussions": "live_confirmed", "comments": "try_supported"},
    "appgallery": {"reviews": "live_confirmed"},
    "imdb": {"reviews": "not_supported"},
    "vk": {"comments": "live_confirmed", "posts": "not_supported"},
    "yahoo": {"comments": "not_supported"},
    "newsweek": {"comments": "not_supported"},
    "kuaishou": {"comments": "live_confirmed"},
    "bilibili": {"comments": "live_confirmed"},
    "rutube": {"comments": "live_confirmed"},
    "douyin": {"comments": "live_confirmed"},
    "ebay": {"reviews": "not_supported"},
    "disqus": {"comments": "try_supported"},
}


def mode_status(platform: str, export_type: str) -> str | None:
    status = MODE_SUPPORT.get(platform, {}).get(export_type)
    if not status:
        return None
    if f"{platform}:{export_type}" in HARD_DISABLED_MODES:
        return "not_supported"
    return "try_supported" if status == "not_supported" else status


def url_form_guidance(platform: str, export_type: str) -> list[str]:
    return MODE_URL_FORM_GUIDANCE.get(f"{platform}:{export_type}") or URL_FORM_GUIDANCE.get(platform) or [
        f"Use a canonical public {platform} URL that opens while signed out for {export_type}.",
        "Remove share-tracking parameters if the first form fails.",
    ]


def capabilities_result() -> dict[str, Any]:
    platforms = []
    for item in PLATFORM_DETAILS:
        all_modes = list(MODE_SUPPORT.get(item["id"], {}))
        export_types = [m for m in all_modes if mode_status(item["id"], m) != "not_supported"]
        statuses = [mode_status(item["id"], m) for m in export_types]
        support = "live_confirmed" if "live_confirmed" in statuses \
            else "conditional" if "conditional" in statuses \
            else "try_supported" if export_types else "not_supported"
        notes = [note for note in item["notes"] if not re.search(r"\bdisabled\b|not exposed until", note, re.IGNORECASE)]
        if "try_supported" in statuses:
            notes.append("Unconfirmed modes remain available as try-supported; a failure is returned with URL-form guidance rather than being preemptively blocked.")
        platforms.append({
            **item,
            "support": support,
            "exportTypes": export_types,
            "notes": notes,
            "urlForms": url_form_guidance(item["id"], export_types[0] if export_types else "comments"),
            "urlFormsByMode": {m: url_form_guidance(item["id"], m) for m in export_types},
            "hosts": hosts_for_platform(item["id"]),
        })
    return {
        "platforms": platforms,
        "modeSupport": {platform: {mode: mode_status(platform, mode) for mode in modes} for platform, modes in MODE_SUPPORT.items()},
        "supportLegend": {
            "live_confirmed": "Completed at least one bounded live test.",
            "conditional": "Recognized or documented, but the tested URL/mode was unreliable.",
            "try_supported": "Documented or recognized but not conclusively verified. The service will try it and explain any failure.",
            "not_supported": "Hard-disabled only after strong evidence of wrong semantics, unavailable required authentication, or an officially inactive collector.",
        },
        "behaviorNotes": [
            "One export mode is used per job.",
            "Record limits are best-effort and may be exceeded by platform batches.",
            "The shared service can impose rolling job-start limits; retry timing is returned when available.",
            "Date filters remain disabled because live tests found them ignored or stripped.",
            "Metadata availability varies by platform and post.",
            "A failed URL form does not prove the collector is unsupported. Try the canonical and alternate forms listed for that platform.",
            "A completed job can still be unusable if it has no records or returns the wrong data shape; inspect fields before relying on experimental results.",
        ],
    }

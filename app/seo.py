import html
import json
from importlib.resources import files


def render_landing_page(canonical_url: str, indexable: bool) -> str:
    structured_data = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": "URL Shortener",
            "applicationCategory": "DeveloperApplication",
            "description": (
                "Open-source URL shortener for creating short links with "
                "optional expiration and click tracking."
            ),
            "operatingSystem": "Any",
            "url": canonical_url,
        }
    ).replace("<", "\\u003c")
    template = files("app").joinpath("static", "index.html").read_text(encoding="utf-8")
    return (
        template.replace("__CANONICAL_URL__", html.escape(canonical_url, quote=True))
        .replace("__ROBOTS__", "index,follow" if indexable else "noindex,nofollow")
        .replace("__STRUCTURED_DATA__", structured_data)
    )

"""Content-hash cache-busting for static assets. Cloudflare (and browsers)
cache /static/* aggressively; since a static asset's URL never otherwise
changes between deploys, a stale cached copy can outlive a redeploy by
hours. Appending ?v=<hash> to the asset URL makes each content change a
new URL instead of relying on cache invalidation.
"""

import hashlib


def content_hash(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:10]
    except OSError:
        return "0"

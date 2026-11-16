"""Genera la demo estática para Amplify Hosting a partir del dashboard local."""

import json
import shutil
from pathlib import Path

from cognito_login import CLIENT_ID, DOMAIN
from dashboard import DASHBOARD_ROUTES, ROOT, page, public_page

OUTPUT = ROOT.parent / "amplify-dist"


def with_auth(document):
    return document.replace(
        "</head>",
        '<meta name="referrer" content="no-referrer">\n'
        '<script src="/auth-config.js" defer></script>\n'
        '<script src="/amplify-auth.js" defer></script>\n</head>',
    )


def build():
    OUTPUT.mkdir(exist_ok=True)
    public = with_auth(public_page()).replace('href="/auth/login"', 'href="/" data-auth="login"')
    public = public.replace('href="/auth/signup"', 'href="/" data-auth="signup"')
    public = public.replace(
        '<div class="public-actions">',
        '<p data-auth-status role="status" hidden></p><div class="public-actions">',
    )
    (OUTPUT / "index.html").write_text(public, encoding="utf-8")

    for resource in DASHBOARD_ROUTES:
        document = with_auth(page(resource)).replace("<body>", '<body hidden data-dashboard>')
        document = document.replace('href=\'/\'', "href='/dashboard.html'")
        document = document.replace('class="brand" href="/"', 'class="brand" href="/dashboard.html"')
        for section in DASHBOARD_ROUTES[1:]:
            document = document.replace(f"href='/{section}'", f"href='/{section}.html'")
        document = document.replace('method="post" action="/auth/logout"', 'data-auth-logout')
        filename = f"{resource or 'dashboard'}.html"
        (OUTPUT / filename).write_text(document, encoding="utf-8")

    for filename in ("dashboard.css", "table-sort.js", "amplify-auth.js"):
        shutil.copyfile(ROOT / filename, OUTPUT / filename)
    config = json.dumps({"domain": DOMAIN, "clientId": CLIENT_ID}, ensure_ascii=True)
    (OUTPUT / "auth-config.js").write_text(f"window.dashboardAuth = {config};\n", encoding="utf-8")


if __name__ == "__main__":
    build()

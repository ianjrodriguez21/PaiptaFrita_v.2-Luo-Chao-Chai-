"""Vista local de ArquiFusionCorp con datos simulados."""

import argparse
import json
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import cognito_login

ROOT = Path(__file__).resolve().parent
SECTIONS = {
    "machines": ("Máquinas", {
        "machine_id": "ID", "machine_type": "Tipo", "current_status": "Estado",
        "operating_hours": "Horas de uso", "temperature": "Temperatura",
        "vibration": "Vibración", "fuel_level_percentage": "Combustible (%)",
    }),
    "incidents": ("Incidentes", {
        "incident_id": "ID", "machine_id": "Máquina", "incident_type": "Tipo",
        "severity": "Severidad", "risk_score": "Riesgo", "status": "Estado",
        "detected_at": "Detección",
    }),
    "technicians": ("Técnicos", {
        "technician_id": "ID", "name": "Nombre", "specialties": "Especialidades",
        "availability": "Disponibilidad", "latitude": "Latitud", "longitude": "Longitud",
    }),
    "parts": ("Repuestos", {
        "part_id": "ID", "name": "Repuesto", "type": "Tipo", "quantity": "Cantidad",
        "minimum_stock": "Stock mínimo", "status": "Estado", "warehouse_location": "Almacén",
    }),
}
DEMO_TENANT = "MINA_A"
TECHNICIAN_VIEW = "technician-demo"
DASHBOARD_ROUTES = ("", *SECTIONS, TECHNICIAN_VIEW)


def display(value):
    if value is None:
        return "—"
    if isinstance(value, list):
        value = ", ".join(map(str, value))
    return escape(str(value))


def table(rows, columns):
    headings = "".join(
        f'<th scope="col"><button class="sort-button" type="button">'
        f'{escape(label)} <span aria-hidden="true">↕</span></button></th>'
        for label in columns.values()
    )
    records = "".join(
        "<tr>" + "".join(
            f'<td data-sort="{escape(str(row[field]), quote=True) if row.get(field) is not None else ""}">'
            f'{display(row.get(field))}</td>' for field in columns
        ) + "</tr>"
        for row in rows
    )
    if not records:
        return "<p class='empty'>Sin registros para mostrar.</p>"
    return (
        '<div class="table-tools">'
        '<label>Buscar en la tabla <input class="table-search" type="search" autocomplete="off"></label>'
        '<span class="table-results" role="status"></span>'
        '</div>'
        '<div class="table-scroll">'
        f'<table><thead><tr>{headings}</tr></thead><tbody>{records}</tbody></table></div>'
    )


def section(resource, data):
    title, columns = SECTIONS[resource]
    return f"<section class='panel'><h2>{title}</h2>{table(data[resource], columns)}</section>"


SEVERITIES = {"critical": "Crítica", "high": "Alta", "medium": "Media", "low": "Baja"}
INCIDENT_STATES = {"open": "Abierto", "assigned": "Asignado", "in_progress": "En atención"}
INCIDENT_TYPES = {"abnormal_vibration": "Vibración anormal"}


def incident_priority(incident):
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    score = incident.get("risk_score")
    return severity_order.get(incident.get("severity"), 4), -score if isinstance(score, (int, float)) else 0


def incident_cards(incidents):
    cards = []
    for incident in sorted(incidents, key=incident_priority)[:3]:
        severity = incident.get("severity")
        label = SEVERITIES.get(severity, "Sin clasificar")
        state = INCIDENT_STATES.get(incident.get("status"), incident.get("status"))
        event = incident.get("incident_type")
        detection = display(INCIDENT_TYPES.get(event, event))
        cards.append(
            f'<article class="priority-card severity-{severity if severity in SEVERITIES else "unknown"}">'
            f'<div class="priority-meta"><span class="severity-label">{label}</span>'
            f'<span>{display(state)}</span></div>'
            f'<h3>{display(incident.get("machine_id"))}</h3>'
            f'<p>{detection}</p>'
            f'<dl><div><dt>Incidente</dt><dd>{display(incident.get("incident_id"))}</dd></div>'
            f'<div><dt>Puntaje de riesgo</dt><dd>{display(incident.get("risk_score"))}</dd></div></dl>'
            '</article>'
        )
    return "".join(cards) or '<p class="empty">No hay incidentes activos.</p>'


def overview(data):
    unresolved = [row for row in data["incidents"] if row.get("status") != "resolved"]
    affected = {row["machine_id"] for row in unresolved if row.get("machine_id")}
    cards = {
        "Máquinas registradas": len(data["machines"]),
        "Máquinas con incidentes activos": sum(row["machine_id"] in affected for row in data["machines"]),
        "Incidentes abiertos": sum(row.get("status") == "open" for row in unresolved),
        "Repuestos con stock crítico": sum(
            row.get("status") in {"low_stock", "out_of_stock"} for row in data["parts"]
        ),
    }
    metrics = "".join(
        f"<article class='metric'><span>{label}</span><strong>{value}</strong></article>"
        for label, value in cards.items()
    )
    severities = "".join(
        f'<li class="severity-{key}"><span>{label}</span>'
        f'<strong>{sum(row.get("severity") == key for row in unresolved)}</strong></li>'
        for key, label in SEVERITIES.items()
    )
    return (
        '<section class="priority-panel" aria-labelledby="priority-title">'
        '<div class="priority-heading"><h2 id="priority-title">Incidentes que requieren atención</h2>'
        "<a class='button' href='/incidents'>Ver todos los incidentes</a></div>"
        f'<div class="priority-grid">{incident_cards(unresolved)}</div></section>'
        f'<div class="metrics">{metrics}</div>'
        '<section class="panel severity-panel"><h2>Incidentes activos por severidad</h2>'
        f'<ul class="severity-totals">{severities}</ul></section>'
    )


def technician_view(data):
    active = [row for row in data["incidents"] if row.get("status") != "resolved"]
    incident = min(active, key=incident_priority, default=None)
    if incident is None:
        return '<section class="panel"><h2>Incidente prioritario</h2><p>No hay incidentes activos.</p></section>'

    machine_id = incident.get("machine_id")
    machine = next((row for row in data["machines"] if row.get("machine_id") == machine_id), None)
    if machine:
        measurements = {
            "Latitud": machine.get("latitude"), "Longitud": machine.get("longitude"),
            "Temperatura": machine.get("temperature"), "Vibración": machine.get("vibration"),
            "Presión": machine.get("pressure"), "Combustible (%)": machine.get("fuel_level_percentage"),
            "Horas de uso": machine.get("operating_hours"),
        }
        details = "".join(
            f"<div><dt>{label}</dt><dd>{display(value)}</dd></div>"
            for label, value in measurements.items()
        )
        telemetry = (
            f'<p>Última lectura: {display(machine.get("timestamp"))}</p>'
            f'<dl class="telemetry-grid">{details}</dl>'
        )
    else:
        telemetry = '<p>La telemetría de esta máquina no está disponible.</p>'
    return (
        '<section class="priority-panel"><h2>Incidente prioritario</h2>'
        f'{incident_cards([incident])}</section>'
        f'<section class="panel"><h2>Ubicación y telemetría</h2>{telemetry}</section>'
    )


def public_page():
    return """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ArquiFusionCorp</title>
<link rel="stylesheet" href="/dashboard.css">
</head>
<body class="public">
<main class="public-main">
  <section class="public-card">
    <span class="brand-mark" aria-hidden="true">A</span>
    <h1>ArquiFusionCorp</h1>
    <p>Consulta el estado de la flota, los incidentes, los técnicos y los repuestos.</p>
    <p>Inicia sesión para acceder a la información.</p>
    <div class="public-actions">
      <a class="button primary" href="/auth/login">Iniciar sesión</a>
      <a class="button" href="/auth/signup">Crear cuenta</a>
    </div>
  </section>
</main>
</body>
</html>"""


def page(resource):
    source = json.loads((ROOT / "mocks" / "operaciones.json").read_text(encoding="utf-8"))
    data = {key: [row for row in source[key] if row.get("tenant_id") == DEMO_TENANT] for key in SECTIONS}
    links = {"": "Resumen", **{key: value[0] for key, value in SECTIONS.items()}}
    is_technician = resource == TECHNICIAN_VIEW
    if is_technician:
        links = {TECHNICIAN_VIEW: "Incidente prioritario"}
    nav = "".join(
        f"<a href='/{key}'{' aria-current=page' if key == resource else ''}>{label}</a>"
        for key, label in links.items()
    )
    if is_technician:
        title = "Ficha del técnico"
        content = technician_view(data)
    else:
        title = links[resource]
        content = section(resource, data) if resource else overview(data)
    demo_views = "".join(
        f"<a class='button' href='/{route}'"
        f"{' aria-current=page' if selected else ''}>{label}</a>"
        for route, label, selected in (
            ("", "Supervisor", not is_technician),
            (TECHNICIAN_VIEW, "Técnico", is_technician),
        )
    )
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} | ArquiFusionCorp</title>
<link rel="stylesheet" href="/dashboard.css">
<script src="/table-sort.js" defer></script>
</head>
<body>
<aside>
  <a class="brand" href='/{TECHNICIAN_VIEW if is_technician else ""}'><span class="brand-mark" aria-hidden="true">A</span>ArquiFusionCorp</a>
  <nav aria-label="Principal">{nav}</nav>
</aside>
<main>
  <header>
    <div><p>Monitoreo de flota</p><h1>{title}</h1></div>
    <div class="header-actions">
      <span class="environment">Organización de prueba: {DEMO_TENANT} · Datos simulados</span>
      <form method="post" action="/auth/logout"><button class="button" type="submit">Cerrar sesión</button></form>
    </div>
  </header>
  <nav class="demo-views" aria-label="Vista de demo">
    <span>Vista de demo</span>{demo_views}
  </nav>
{content}
</main>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def redirect(self, location, *cookies):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        for item in cookies:
            self.send_header("Set-Cookie", item)
        self.end_headers()

    def log_request(self, code="-", size="-"):
        if urlsplit(self.path).path != "/auth/callback":
            super().log_request(code, size)

    def do_POST(self):
        if urlsplit(self.path).path != "/auth/logout":
            self.send_error(404)
            return
        if self.headers.get("Origin") != cognito_login.SIGNED_OUT_URL.rstrip("/"):
            self.send_error(403)
            return
        self.redirect(
            cognito_login.sign_out_url(),
            cognito_login.cookie(cognito_login.SESSION_COOKIE, "", 0),
            cognito_login.cookie(cognito_login.FLOW_COOKIE, "", 0),
        )

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path in {"/auth/login", "/auth/signup"}:
            if self.headers.get("Host") != "localhost:8080":
                self.redirect(cognito_login.CALLBACK.removesuffix("/auth/callback") + url.path)
                return
            self.redirect(*cognito_login.start(sign_up=url.path == "/auth/signup"))
            return
        if url.path == "/auth/callback":
            try:
                session = cognito_login.complete(self.headers, parse_qs(url.query))
            except (OSError, ValueError, KeyError, TypeError):
                self.redirect("/", cognito_login.cookie(cognito_login.FLOW_COOKIE, "", 0))
                return
            self.redirect("/", session, cognito_login.cookie(cognito_login.FLOW_COOKIE, "", 0))
            return
        resource = url.path.strip("/")
        signed_in = cognito_login.active(self.headers)
        if resource in {"dashboard.css", "table-sort.js"}:
            content = (ROOT / resource).read_bytes()
            kind = "text/css" if resource == "dashboard.css" else "text/javascript"
        elif resource in DASHBOARD_ROUTES:
            if resource and not signed_in:
                self.redirect("/")
                return
            content = (page(resource) if signed_in else public_page()).encode("utf-8")
            kind = "text/html"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler) as server:
        print(f"Dashboard local en http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

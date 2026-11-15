"""Vista local de ArquiFusionCorp con datos simulados."""

import argparse
import json
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

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
TENANT = "MINA_A"


def display(value):
    if value is None:
        return "—"
    if isinstance(value, list):
        value = ", ".join(map(str, value))
    return escape(str(value))


def table(rows, columns):
    headings = "".join(f"<th scope='col'>{escape(label)}</th>" for label in columns.values())
    records = "".join(
        "<tr>" + "".join(f"<td>{display(row.get(field))}</td>" for field in columns) + "</tr>"
        for row in rows
    )
    if not records:
        return "<p class='empty'>Sin registros para mostrar.</p>"
    return f"<div class='table-scroll'><table><thead><tr>{headings}</tr></thead><tbody>{records}</tbody></table></div>"


def section(resource, data):
    title, columns = SECTIONS[resource]
    return f"<section class='panel'><h2>{title}</h2>{table(data[resource], columns)}</section>"


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
    severities = [
        {"severity": label, "count": sum(row.get("severity") == key for row in unresolved)}
        for key, label in [("critical", "Crítica"), ("high", "Alta"), ("medium", "Media"), ("low", "Baja")]
    ]
    breakdown = table(severities, {"severity": "Severidad", "count": "Cantidad"})
    return (
        f"<div class='metrics'>{metrics}</div>"
        f"<section class='panel'><h2>Incidentes activos por severidad</h2>{breakdown}</section>"
        + section("incidents", data)
    )


def page(resource):
    source = json.loads((ROOT / "mocks" / "operaciones.json").read_text(encoding="utf-8"))
    data = {key: [row for row in source[key] if row.get("tenant_id") == TENANT] for key in SECTIONS}
    links = {"": "Resumen", **{key: value[0] for key, value in SECTIONS.items()}}
    nav = "".join(
        f"<a href='/{key}'{' aria-current=page' if key == resource else ''}>{label}</a>"
        for key, label in links.items()
    )
    title = links[resource]
    content = section(resource, data) if resource else overview(data)
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} | ArquiFusionCorp</title>
<link rel="stylesheet" href="/dashboard.css">
</head>
<body>
<aside><div class="brand">ArquiFusionCorp</div><nav aria-label="Principal">{nav}</nav></aside>
<main><header><div><p>Monitoreo de flota</p><h1>{title}</h1></div>
<span class="environment">Datos simulados · {TENANT}</span></header>
{content}
</main>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        resource = urlsplit(self.path).path.strip("/")
        if resource == "dashboard.css":
            content = (ROOT / "dashboard.css").read_bytes()
            kind = "text/css"
        elif not resource or resource in SECTIONS:
            content = page(resource).encode("utf-8")
            kind = "text/html"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
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

# Lambdas del dashboard

Abre el repositorio en VS Code y coloca esta carpeta en su raíz como `front end`. Las funciones son Python 3.12 y reciben eventos de API Gateway HTTP API con payload 2.0. Desde la raíz puedes correr `python3 -m unittest discover -s "front end" -p "test_*.py"`. Las pruebas simulan API Gateway y DynamoDB y funcionan sin credenciales AWS.

| Ruta GET propuesta | Módulo en `frontend_lambdas` | Fuente de datos |
| --- | --- | --- |
| `/machines` | `ObtenerMaquinas` | `MACHINES_TABLE_NAME` / `MaquinaTable` |
| `/machines/{machine_id}` | `ObtenerMaquinaDetalle` | `MACHINES_TABLE_NAME` / `MaquinaTable` |
| `/incidents` | `ObtenerIncidentes` | `INCIDENTS_TABLE_NAME` / `IncidentesTable` |
| `/maintenance` | `ObtenerMantenimientos` | `MAINTENANCE_TABLE_NAME` / tabla pendiente |
| `/technicians` | `ObtenerTecnicos` | `TECHNICIANS_TABLE_NAME` / `TecnicosTable` |
| `/parts` | `ObtenerRepuestos` | `PARTS_TABLE_NAME` / `RepuestosTable` |
| `/dashboard` | `ObtenerDashboard` | Máquinas, incidentes, mantenimientos, utilización, eficacia y predicción |

Las listas devuelven `items` y `next_cursor`. Usa `?limit=25` y envía el cursor codificado como parámetro `cursor` al pedir la página siguiente. El detalle devuelve un objeto. El dashboard entrega la primera página de cada sección y marca con `not_configured` las fuentes que carecen de variable de entorno. Los valores del dashboard provienen de las tablas operativas.

## Vista local en VS Code

Abre `front end` como carpeta en VS Code. Ejecuta `python3 dashboard.py` en su terminal integrada y entra a <http://127.0.0.1:8080/>. También puedes usar la configuración **Dashboard local** con la extensión Python Debugger. La vista muestra datos simulados de MINA_A y solo escucha en loopback. Para detenerla, pulsa Ctrl+C.

La navegación local incluye resumen, máquinas, incidentes, mantenimientos, técnicos y repuestos. Las secciones leen `mocks/operaciones.json`. Quedan pendientes Cognito, el servicio de datos por API Gateway, los indicadores de utilización y predicción y las vistas de detalle e historial.

## Datos de prueba

`mocks/operaciones.json` conserva los ejemplos de telemetría, incidentes, mantenimientos, técnicos y repuestos del esquema del equipo. Son datos simulados para preparar las vistas y probarlas en local. Cada ejemplo corresponde a `MINA_A`. El archivo agrupa las filas por dominio y conserva los nombres y valores de sus campos.

Las rutas de la tabla siguen el esquema del equipo. En la integración de `/machines/{machine_id}`, usa ese mismo nombre para el parámetro de ruta al configurar API Gateway.

## Conectar a AWS

- Empaqueta `frontend_lambdas/` en la raíz del ZIP. Configura un handler por módulo, por ejemplo `frontend_lambdas.ObtenerMaquinas.lambda_handler`.
- Protege cada ruta con el autorizador JWT de API Gateway y Cognito. Los handlers esperan `sub` y `custom:tenant_id` en los claims validados. Si el equipo usa access tokens, confirma que Cognito emita el claim de tenant en ese token. La asignación de tenant debe estar controlada por el servidor y fuera de los atributos editables por los usuarios. Las consultas usan ese claim como clave de partición.
- Configura las variables de entorno con los nombres físicos del stage. Para el dashboard agrega `UTILIZATION_TABLE_NAME` (`UtilizacionTable`), `EFFICACY_TABLE_NAME` (`EficaciaTable`) y `PREDICTIONS_TABLE_NAME` (`PrediccionRepuestosTable`).
- Con la tabla de mantenimientos disponible, configura `MAINTENANCE_TABLE_NAME` y `MAINTENANCE_SORT_KEY`. La paginación de esa tabla espera `tenant_id` como clave de partición y una clave de ordenamiento de tipo String.
- Otorga al rol de ejecución `dynamodb:Query` sobre las tablas consultadas y `dynamodb:GetItem` sobre la tabla de máquinas para el detalle. Antes de exponer las rutas, valida la matriz de roles y los campos que puede ver cada rol. Esta primera versión aplica aislamiento por organización y limita los campos devueltos para técnicos.
- Empieza la autenticación con la interfaz de inicio de sesión de Cognito y define el aprovisionamiento de usuarios y sus roles de acuerdo con el esquema del equipo. Crear usuarios, el inicio de sesión, la interfaz web y el despliegue quedan pendientes.

## Subir a main

Desde la raíz de tu clon y con permisos de escritura, revisa `git status --short --branch` y confirma que estás en `main`. Luego ejecuta:

```bash
git pull --ff-only origin main
git add -- "front end"
git diff --cached --name-only
```

Verifica que el diff muestre únicamente archivos de `front end/`. Después ejecuta `git commit -m "Add dashboard query Lambdas"` y `git push origin main`.

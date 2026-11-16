# Lambdas del dashboard

Abre el repositorio en VS Code y coloca esta carpeta en su raíz como `front end`. Las funciones son Python 3.12 y reciben eventos de API Gateway HTTP API con payload 2.0. Desde la raíz puedes correr `python3 -m unittest discover -s "front end" -p "test_*.py"`. Las pruebas simulan API Gateway y DynamoDB y funcionan sin credenciales AWS.

| Ruta GET propuesta | Módulo en `frontend_lambdas` | Fuente de datos |
| --- | --- | --- |
| `/machines` | `ObtenerMaquinas` | `MACHINES_TABLE_NAME` / `MaquinaTable` |
| `/machines/{machine_id}` | `ObtenerMaquinaDetalle` | `MACHINES_TABLE_NAME` / `MaquinaTable` |
| `/incidents` | `ObtenerIncidentes` | `INCIDENTS_TABLE_NAME` / `IncidentesTable` |
| `/technicians` | `ObtenerTecnicos` | `TECHNICIANS_TABLE_NAME` / `TecnicosTable` |
| `/parts` | `ObtenerRepuestos` | `PARTS_TABLE_NAME` / `RepuestosTable` |
| `/dashboard` | `ObtenerDashboard` | Máquinas, incidentes, utilización, eficacia y predicción |

Las listas devuelven `items` y `next_cursor`. Usa `?limit=25` y envía el cursor codificado como parámetro `cursor` al pedir la página siguiente. El detalle devuelve un objeto. El dashboard entrega la primera página de cada sección y marca con `not_configured` las fuentes que carecen de variable de entorno. Los valores del dashboard provienen de las tablas operativas.

## Vista local en VS Code

Abre `front end` como carpeta en VS Code. Ejecuta `python3 dashboard.py` en su terminal integrada y entra a <http://localhost:8080/>. También puedes usar la configuración **Dashboard local** con la extensión Python Debugger. La vista muestra datos simulados de MINA_A y solo escucha en loopback. Para detenerla, pulsa Ctrl+C.

La navegación local incluye resumen, máquinas, incidentes, técnicos y repuestos. Las secciones leen `mocks/operaciones.json`. Quedan pendientes el servicio de datos por API Gateway, los indicadores de utilización y predicción y las vistas de detalle e historial.

Para probar autenticación, inicia el servidor en el puerto 8080 y abre la URL con el host `localhost`. Usa **Iniciar sesión** con una cuenta del User Pool o **Crear cuenta** con un correo al que tengas acceso. Cognito debe permitir autorregistro y tener registrado `http://localhost:8080/auth/callback`. Para usar otro User Pool, configura `COGNITO_LOGIN_DOMAIN` y `COGNITO_CLIENT_ID` antes de iniciar el servidor.

## Datos de prueba

`mocks/operaciones.json` conserva los ejemplos de telemetría, incidentes, técnicos y repuestos del esquema del equipo. Son datos simulados para preparar las vistas y probarlas en local. Cada ejemplo corresponde a `MINA_A`. El archivo agrupa las filas por dominio y conserva los nombres y valores de sus campos.

Las rutas de la tabla siguen el esquema del equipo. En la integración de `/machines/{machine_id}`, usa ese mismo nombre para el parámetro de ruta al configurar API Gateway.

## Conectar a AWS

- Empaqueta `frontend_lambdas/` en la raíz del ZIP. Configura un handler por módulo, por ejemplo `frontend_lambdas.ObtenerMaquinas.lambda_handler`.
- Protege cada ruta con el autorizador JWT de API Gateway y Cognito. Los handlers esperan `sub` y `custom:tenant_id` en los claims validados. Si el equipo usa access tokens, confirma que Cognito emita el claim de tenant en ese token. La asignación de tenant debe estar controlada por el servidor y fuera de los atributos editables por los usuarios. Las consultas usan ese claim como clave de partición.
- Configura las variables de entorno con los nombres físicos del stage. Para el dashboard agrega `UTILIZATION_TABLE_NAME` (`UtilizacionTable`), `EFFICACY_TABLE_NAME` (`EficaciaTable`) y `PREDICTIONS_TABLE_NAME` (`PrediccionRepuestosTable`).
- Otorga al rol de ejecución `dynamodb:Query` sobre las tablas consultadas y `dynamodb:GetItem` sobre la tabla de máquinas para el detalle. Antes de exponer las rutas, valida la matriz de roles y los campos que puede ver cada rol. Esta primera versión aplica aislamiento por organización y limita los campos devueltos para técnicos.
- El login y el registro funcionan en la vista local con el cliente público de Cognito configurado. El despliegue del dashboard, las rutas de API y el aprovisionamiento de perfiles operativos siguen pendientes.

## Subir a main

Desde la raíz de tu clon y con permisos de escritura, revisa `git status --short --branch` y confirma que estás en `main`. Luego ejecuta:

```bash
git pull --ff-only origin main
git add -- "front end"
git --no-pager diff --cached --name-only
```

Verifica que el diff muestre únicamente archivos de `front end/`. Después ejecuta `git commit -m "Add dashboard query Lambdas"` y `git push origin main`.

## Alta de perfiles

Con el registro por cuenta propia habilitado en Cognito, el dashboard local ofrece **Crear cuenta** junto a **Iniciar sesión**. Cognito recibe el correo y la contraseña y verifica el correo. Las cuentas nuevas carecen de una organización asignada. Los datos del dashboard local siguen siendo simulados.

La Lambda `CrearUsuario` que se incluye corresponde al alta administrativa de perfiles. El alta automática del perfil pendiente tras el primer login todavía requiere implementación.

Para activar el guardado, define `t_users` con claves String `tenant_id` y `user_id`, configura `USERS_TABLE_NAME` y `TECHNICIANS_TABLE_NAME`, y registra `POST /users` con el handler `frontend_lambdas.CrearUsuario.lambda_handler` y el mismo autorizador JWT de las consultas. El rol de ejecución necesita permisos para las operaciones `dynamodb:ConditionCheckItem` y `dynamodb:PutItem` que ejecuta la transacción. Crea el primer perfil administrador activo de la organización de forma controlada con su `sub` de Cognito. La Lambda comprueba ese perfil en la misma transacción que registra al usuario.

Antes de habilitar la ruta, configura el mapeo de `custom:tenant_id` para el token validado y la verificación de que el `sub` y correo ingresados corresponden a una cuenta del mismo tenant. La ruta de alta administrativa permanece pendiente de integración.

## Cerrar sesión

En el cliente público `dashboard-web` de Cognito agrega `http://localhost:8080/` a las URL de cierre de sesión permitidas. El botón **Cerrar sesión** borra la sesión de la app y redirige al cierre de Cognito, que devuelve al usuario a esa URL. Las rutas del dashboard local requieren una sesión. El acceso a datos reales se controla por separado en la API.

## Publicar la demo en Amplify

Conecta el repositorio y la rama `main` en Amplify, con la raíz del repo como directorio de compilación. `amplify.yml` ejecuta `python3 "front end/build_amplify.py"` y publica solo `amplify-dist`. Este build usa los datos simulados incluidos en el repositorio. El HTML resultante es público y no debe contener datos reales. La comprobación de sesión del navegador sirve para la navegación de la demo. Los datos reales deberán venir de una API con autorización del lado del servidor.

En el cliente de aplicación de Cognito agrega `https://main.d2pegxgyu22777.amplifyapp.com/` como URL de devolución de llamada y como URL de cierre de sesión permitida. Conserva las dos URL de localhost para el servidor Python. El build acepta `COGNITO_LOGIN_DOMAIN` y `COGNITO_CLIENT_ID` como variables de Amplify para cambiar de User Pool. Usa el cliente público sin secreto con Authorization Code + PKCE.

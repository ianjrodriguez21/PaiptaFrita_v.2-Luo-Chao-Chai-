# ArquiFusionCorp
## Sistema Inteligente de Monitoreo y Mantenimiento Predictivo Industrial

# Integrantes
- Ian Jesus Rodriguez Santayana (Seccion 1)
- Tommy Bryan Loo Leon (Seccion 3)
- Vannia Fatima Curay Mauricio (Seccion 1)

### Descripción del proyecto

**ArquiFusionCorp** es un proyecto de monitoreo inteligente y mantenimiento predictivo de maquinaria industrial que busca mejorar la eficiencia, seguridad y continuidad de las operaciones.

Está orientado a empresas de sectores como minería, construcción, agricultura, infraestructura y energía, donde el funcionamiento adecuado de los equipos es fundamental para la productividad.

El proyecto aprovecha los datos generados por las máquinas para identificar comportamientos anormales, anticipar posibles fallas y proporcionar información que facilite la toma de decisiones sobre el mantenimiento.

### Finalidad del proyecto

La finalidad principal de **ArquiFusionCorp** es reducir las fallas inesperadas de la maquinaria industrial mediante la detección temprana de anomalías y la evaluación de riesgos operativos.

En lugar de depender únicamente del mantenimiento correctivo, el proyecto busca fomentar estrategias preventivas y predictivas que permitan actuar antes de que ocurran averías graves.

De esta manera, se pretende disminuir los tiempos de inactividad, optimizar los recursos disponibles, prolongar la vida útil de los equipos y mejorar la seguridad de las operaciones.

### Objetivos principales

- **Monitoreo de maquinaria:** supervisar variables operativas como temperatura, vibración, presión y horas de funcionamiento.
- **Detección de anomalías:** identificar comportamientos inusuales que puedan indicar posibles problemas.
- **Predicción de fallas:** analizar patrones de funcionamiento para anticipar situaciones de riesgo.
- **Recomendaciones de mantenimiento:** proporcionar información para planificar intervenciones preventivas y correctivas.
- **Gestión de incidentes:** facilitar el seguimiento de problemas detectados.
- **Optimización de recursos:** apoyar la administración de técnicos, repuestos e intervenciones.
- **Análisis histórico:** conservar información operativa para evaluar tendencias y mejorar las decisiones.
- **Gestión de múltiples organizaciones:** permitir el seguimiento de equipos pertenecientes a diferentes empresas.

### Impacto esperado

ArquiFusionCorp busca contribuir a una gestión industrial más eficiente, reduciendo costos de mantenimiento, interrupciones inesperadas y riesgos operativos.

El proyecto se desarrolla como un prototipo académico. Sus resultados predictivos requieren validación antes de aplicarse a decisiones críticas en entornos industriales reales.

---

## Despliegue del proyecto

ArquiFusionCorp utiliza **Serverless Framework v4** para administrar y desplegar su infraestructura en AWS.

El proyecto está diseñado para funcionar mediante servicios serverless, sin necesidad de utilizar Docker ni contenedores para empaquetar la integración con Azure.

### Requisitos previos

Para desplegar el proyecto se necesita:

- Una cuenta AWS activa con credenciales válidas y permisos suficientes.
- Node.js y Serverless Framework v4.
- El plugin `serverless-step-functions`.
- Acceso al repositorio de GitHub.
- El rol IAM `LabRole`, utilizado actualmente por el proyecto.
- Los endpoints de Amazon SageMaker requeridos para la inferencia predictiva.
- Las credenciales de Azure configuradas de forma segura en AWS Secrets Manager para habilitar la replicación multicloud.

**Importante:** en una cuenta AWS diferente, `LabRole`, los endpoints de SageMaker y las credenciales de Azure no se crean automáticamente con la configuración actual. Es necesario prepararlos antes de utilizar todas las funcionalidades.

### 1. Clonar el repositorio

```bash
git clone https://github.com/ianjrodriguez21/PaiptaFrita_v.2-Luo-Chao-Chai-.git
cd PaiptaFrita_v.2-Luo-Chao-Chai-
```

### 2. Instalar Serverless Framework

```bash
npm install -g serverless
```

Instalar las dependencias del proyecto, incluido `serverless-step-functions`, cuando estén declaradas en `package.json`:

```bash
npm install
```

Si el plugin no está instalado, puede agregarse mediante:

```bash
npm install --save-dev serverless-step-functions
```

### 3. Configurar AWS

El entorno desde el cual se realizará el despliegue debe disponer de credenciales AWS válidas y autorización para crear y actualizar los recursos del proyecto.

Se puede comprobar la identidad activa con:

```bash
aws sts get-caller-identity
```

No se deben publicar ni compartir las claves privadas, los tokens de sesión o las credenciales de acceso.

### 4. Desplegar mediante Serverless

Desde la raíz del proyecto:

```bash
serverless deploy --stage dev --region us-east-1
```

Serverless utiliza la configuración de `serverless.yml` para gestionar los recursos declarados, entre ellos AWS Lambda, API Gateway, DynamoDB, Amazon S3, Step Functions y Amazon EventBridge.

El despliegue queda sujeto a los permisos de la cuenta AWS y a la disponibilidad de los recursos externos requeridos.

Para verificar el estado del stack:

```bash
aws cloudformation describe-stacks \
  --stack-name arquifusioncorp-dev \
  --region us-east-1 \
  --query "Stacks[0].StackStatus" \
  --output text
```

Un despliegue exitoso debe mostrar `CREATE_COMPLETE` o `UPDATE_COMPLETE`, según corresponda.

---

## Seguridad de las credenciales de Azure

ArquiFusionCorp incorpora una integración entre Amazon S3 y Azure Blob Storage para conservar copias de información operativa.

Por buenas prácticas de seguridad, **las credenciales privadas de Microsoft Azure no están almacenadas en este repositorio de GitHub**.

Publicar estas credenciales en archivos como `serverless.yml`, código Python o documentación permitiría que terceros pudieran utilizarlas indebidamente.

En su lugar, la integración utiliza **AWS Secrets Manager**, que permite almacenar las credenciales de forma segura y recuperarlas durante la ejecución de AWS Lambda.

El secreto utilizado en el entorno de desarrollo se identifica mediante:

```text
arquifusioncorp/dev/azure-blob
```

Este secreto contiene las siguientes claves:

```text
AZURE_TENANT_ID
AZURE_CLIENT_ID
AZURE_CLIENT_SECRET
AZURE_STORAGE_ACCOUNT
```

Los valores reales no se incluyen en el proyecto.

La Lambda `replicarS3Azure` obtiene las credenciales directamente desde AWS Secrets Manager y las utiliza para autenticarse con Microsoft Entra ID y escribir archivos en Azure Blob Storage.

### Despliegue desde otra cuenta AWS

Si el proyecto se despliega desde otra cuenta AWS, el secreto existente en la cuenta original no estará disponible automáticamente.

Para habilitar la integración con Azure, será necesario configurar en la cuenta de destino un secreto con el nombre esperado y las credenciales correspondientes, o establecer un mecanismo de acceso entre cuentas con los permisos necesarios.

**El ARN de un secreto no concede acceso por sí solo.** El acceso entre cuentas requiere políticas IAM, permisos sobre el secreto y una configuración compatible de AWS KMS.

En consecuencia, ejecutar `serverless deploy` desde otra cuenta puede crear la infraestructura AWS declarada, pero **no garantiza que la integración con Azure ni los modelos de SageMaker funcionen sin preparación adicional**.

Esta separación protege las credenciales y evita incluir información confidencial en el control de versiones.

---

## Conclusión

**ArquiFusionCorp** propone un enfoque de mantenimiento industrial inteligente y proactivo mediante el monitoreo de maquinaria, el análisis predictivo y la automatización de procesos.

Su implementación utiliza servicios serverless y contempla una integración multicloud entre AWS y Azure, priorizando la automatización de infraestructura, la gestión centralizada de información y la protección de credenciales sensibles.
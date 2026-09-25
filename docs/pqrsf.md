# PQRSF

El módulo PQRSF expone un formulario público aislado de la sesión de Entra y
un conjunto de operaciones administrativas protegidas por cookie de auth y el
rol local `admin`.

## Contrato HTTP

La base local es `/api/v1`.

| Método | Ruta | Acceso | Perfil de rate limit |
| --- | --- | --- | --- |
| `GET` | `/public/pqrsf/catalogs` | Público | `relaxed` |
| `POST` | `/public/pqrsf/requests` | Público | `strict` |
| `GET` | `/pqrsf/requests` | Admin | `medium` |
| `GET` | `/pqrsf/requests/{request_id}` | Admin | `medium` |
| `GET` | `/pqrsf/requests/{request_id}/attachments/{attachment_id}/download` | Admin | `medium` |
| `GET` | `/sites` | Admin | `medium` |
| `POST` | `/sites` | Admin | `medium` |
| `PATCH` | `/sites/{site_id}` | Admin | `medium` |
| `DELETE` | `/sites/{site_id}` | Admin | `medium` |

El catálogo devuelve `request_types` y `sites`. Cada tipo contiene `id`,
`code`, `name`, `allows_reason` y `allows_attachments`; la interfaz debe usar
esas dos banderas para mostrar u ocultar campos y no hardcodear la lógica
condicional.

Los cinco tipos iniciales son `peticion`, `queja`, `reclamo`, `sugerencia` y
`felicitacion`. El catálogo de sedes inicial contiene Aco, Apartadó,
Barranquilla, Bogotá, Bucaramanga, Cartagena, JDH Market Apartadó, JDH Market
Valledupar, Medellín, Pereira, Valledupar y Yumbo.

## Envío público

El `POST` usa `multipart/form-data`. Debe incluir un UUID nuevo en el header
`Idempotency-Key`; el servidor lo utiliza para que reintentos del navegador no
dupliquen la solicitud.

Campos de formulario:

- `request_type_id`: UUID del catálogo.
- `site_id`: UUID de la sede.
- `full_name`, `business_name`, `nit`, `address`, `department`, `city` y
  `email`: obligatorios.
- `description`: obligatoria para todos los tipos.
- `reason`: obligatoria para petición, queja y reclamo; no debe enviarse para
  sugerencia o felicitación.
- `images`: cero o más archivos sólo para petición, queja y reclamo.

El servidor devuelve `201` con `{ "id", "reference", "created_at" }`. Los
errores de validación usan el formato de errores de la API y normalmente
responden `422`; un límite excedido responde `413`, una clave de idempotencia
reutilizada con datos distintos responde `409`, rate limiting responde `429` y
la caída de Redis, S3 o ClamAV responde `503`.

## Seguridad de imágenes

Se aceptan únicamente JPG/JPEG, PNG y WebP. La API comprueba nombre,
extensión, MIME y formato real, limita cada archivo a 5 MiB, permite máximo
cinco archivos y 25 MiB por solicitud, limita la imagen decodificada a 20
millones de píxeles y rechaza imágenes animadas. Pillow decodifica y re-encoda
el contenido sin EXIF antes de que ClamAV lo analice. Sólo después se guarda en
un bucket S3/MinIO privado bajo una clave generada por el servidor.

Los metadatos nunca exponen la clave privada. La descarga sólo está disponible
para un administrador mediante el endpoint protegido de descarga.

## Rate limiting

Los perfiles son configurables por ambiente:

- `strict`: 5 solicitudes cada 10 minutos.
- `medium`: 30 solicitudes por minuto.
- `relaxed`: 120 solicitudes por minuto.

El contador usa una ventana deslizante en Redis. Si Redis no responde, el
endpoint falla cerrado con `503`; no se acepta un envío público sin el control
de abuso disponible.

## Desarrollo local

Para usar la API desde el host, levanta la infraestructura y ejecuta la
migración:

```powershell
docker compose up -d
poetry install
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Para ejecutar también la API y ClamAV en contenedores usa:

```powershell
docker compose --profile app up -d --build
```

El formulario público de la UI debe vivir en `/pqrsf`, fuera del grupo de
layout autenticado. Puede reutilizar los tokens visuales y la fuente de la
plataforma, pero no debe montar sidebar, header, sesión ni navegación privada.
Las llamadas públicas deben omitir credenciales (`credentials: "omit"`); las
operaciones administrativas conservan `credentials: "include"`.

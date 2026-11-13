# Límites de tráfico por nivel

Todos los endpoints de la API tienen un nivel de límite, aplicado por un middleware global
(`app/core/rate_limit_middleware.py`) **antes** de enrutar y de leer el cuerpo: una solicitud que
excede su cupo cuesta un contador en Redis y nada más.

| Nivel | Para qué | Cupo por defecto (por dirección) | Variables |
|---|---|---|---|
| `read` | Consultas (`GET`). Una pantalla dispara varias a la vez | 600 cada 60 s | `RATE_LIMIT_READ_REQUESTS` / `_WINDOW_SECONDS` |
| `relaxed` | Cambios (`POST`, `PUT`, `PATCH`, `DELETE`) | 120 cada 60 s | `RATE_LIMIT_RELAXED_REQUESTS` / `_WINDOW_SECONDS` |
| `strict` | Lo costoso o abierto al público | 5 cada 600 s | `RATE_LIMIT_STRICT_REQUESTS` / `_WINDOW_SECONDS` |

Cada nivel cuenta aparte por dirección: una pantalla que consulta mucho no gasta el cupo de los
cambios. El formulario público suma además un tope global y, opcionalmente, CAPTCHA
(`docs/pqrsf.md`, sección «Seguridad del formulario público»). El nivel `medium` desapareció: sus endpoints pasaron a `read` o
`relaxed` según su método.

## Cómo se asigna el nivel

El nivel sigue al método HTTP: `GET` y `HEAD` son `read`; los demás métodos, `relaxed`. Un
endpoint nuevo queda limitado desde su primer día sin que nadie tenga que acordarse. Las
excepciones están en un solo lugar, `RULES` de `app/core/rate_policy.py`, cada una con su motivo:

- `strict`: el envío del formulario público, la exportación de casos, el refresco de la foto de
  perfil y la sincronización manual del directorio (ambos llaman a Microsoft Graph).
- Sin límite: las sondas `/health/live` y `/health/ready` (el orquestador siempre debe obtener
  respuesta) y las solicitudes `OPTIONS` de CORS.

Las conexiones WebSocket (presencia de proyectos) no pasan por este middleware.

## Qué ve el cliente

Toda respuesta limitada lleva `X-RateLimit-Policy` (el nivel), `X-RateLimit-Limit` y
`X-RateLimit-Remaining`. Al excederse: `429` con `Retry-After` en segundos y las cabeceras CORS,
para que el navegador pueda leerlo. Si Redis no responde, `503` y no se admite nada (las sondas
siguen respondiendo).

## Despliegue

- **`RATE_LIMIT_TRUSTED_PROXY_IPS`** debe listar los proxies delante de la API. Si no, todos los
  usuarios comparten la dirección del proxy y un solo balde: el cupo se agota para todos a la vez.
- El cupo es por dirección. Una oficina detrás de una misma salida a Internet comparte el suyo;
  por eso `read` y `relaxed` son holgados y solo `strict` es estrecho. Se ajustan por variable
  de entorno sin tocar código.

## Cambiar el nivel de un endpoint

Agregar una `Rule` en `app/core/rate_policy.py` y regenerar la tabla de abajo con
`python -m app.core.rate_policy --write` (una prueba falla si la tabla no coincide con la API).

## Nivel de cada endpoint

<!-- niveles:inicio -->
| Método | Ruta | Nivel | Motivo |
|---|---|---|---|
| `GET` | `/health/live` | `sin límite` | Las sondas del orquestador siempre deben recibir respuesta |
| `GET` | `/health/ready` | `sin límite` | Las sondas del orquestador siempre deben recibir respuesta |
| `GET` | `/v1/app-monitoring/applications` | `read` |  |
| `POST` | `/v1/app-monitoring/applications` | `relaxed` |  |
| `DELETE` | `/v1/app-monitoring/applications/{application_id}` | `relaxed` |  |
| `GET` | `/v1/app-monitoring/applications/{application_id}` | `read` |  |
| `PATCH` | `/v1/app-monitoring/applications/{application_id}` | `relaxed` |  |
| `POST` | `/v1/app-monitoring/applications/{application_id}/check` | `relaxed` |  |
| `GET` | `/v1/app-monitoring/applications/{application_id}/checks` | `read` |  |
| `POST` | `/v1/app-monitoring/check` | `relaxed` |  |
| `GET` | `/v1/app-monitoring/overview` | `read` |  |
| `GET` | `/v1/areas` | `read` |  |
| `POST` | `/v1/areas` | `relaxed` |  |
| `DELETE` | `/v1/areas/{slug}` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}` | `read` |  |
| `PATCH` | `/v1/areas/{slug}` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}/folders` | `read` |  |
| `POST` | `/v1/areas/{slug}/folders` | `relaxed` |  |
| `DELETE` | `/v1/areas/{slug}/folders/{folder_id}` | `relaxed` |  |
| `PATCH` | `/v1/areas/{slug}/folders/{folder_id}` | `relaxed` |  |
| `POST` | `/v1/areas/{slug}/folders/{folder_id}/documents` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}/members` | `read` |  |
| `POST` | `/v1/areas/{slug}/members` | `relaxed` |  |
| `DELETE` | `/v1/areas/{slug}/members/{member_id}` | `relaxed` |  |
| `PATCH` | `/v1/areas/{slug}/members/{member_id}` | `relaxed` |  |
| `DELETE` | `/v1/areas/{slug}/modules/{module}` | `relaxed` |  |
| `PUT` | `/v1/areas/{slug}/modules/{module}` | `relaxed` |  |
| `DELETE` | `/v1/areas/{slug}/project-epics/{epic_id}` | `relaxed` |  |
| `PATCH` | `/v1/areas/{slug}/project-epics/{epic_id}` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}/project-folders` | `read` |  |
| `POST` | `/v1/areas/{slug}/project-folders` | `relaxed` |  |
| `DELETE` | `/v1/areas/{slug}/project-folders/{folder_id}` | `relaxed` |  |
| `PATCH` | `/v1/areas/{slug}/project-folders/{folder_id}` | `relaxed` |  |
| `DELETE` | `/v1/areas/{slug}/project-tasks/{task_id}` | `relaxed` |  |
| `PATCH` | `/v1/areas/{slug}/project-tasks/{task_id}` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}/projects` | `read` |  |
| `POST` | `/v1/areas/{slug}/projects` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}/projects/access` | `read` |  |
| `GET` | `/v1/areas/{slug}/projects/catalog` | `read` |  |
| `GET` | `/v1/areas/{slug}/projects/people` | `read` |  |
| `DELETE` | `/v1/areas/{slug}/projects/{project_id}` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}/projects/{project_id}` | `read` |  |
| `PATCH` | `/v1/areas/{slug}/projects/{project_id}` | `relaxed` |  |
| `POST` | `/v1/areas/{slug}/projects/{project_id}/archive` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}/projects/{project_id}/epics` | `read` |  |
| `POST` | `/v1/areas/{slug}/projects/{project_id}/epics` | `relaxed` |  |
| `POST` | `/v1/areas/{slug}/projects/{project_id}/restore` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}/projects/{project_id}/tasks` | `read` |  |
| `POST` | `/v1/areas/{slug}/projects/{project_id}/tasks` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}/roles` | `read` |  |
| `POST` | `/v1/areas/{slug}/roles` | `relaxed` |  |
| `DELETE` | `/v1/areas/{slug}/roles/{role_id}` | `relaxed` |  |
| `PATCH` | `/v1/areas/{slug}/roles/{role_id}` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}/roles/{role_id}/permissions` | `read` |  |
| `PUT` | `/v1/areas/{slug}/roles/{role_id}/permissions` | `relaxed` |  |
| `DELETE` | `/v1/areas/{slug}/tag-colors` | `relaxed` |  |
| `GET` | `/v1/areas/{slug}/tag-colors` | `read` |  |
| `PUT` | `/v1/areas/{slug}/tag-colors` | `relaxed` |  |
| `GET` | `/v1/auth/callback` | `read` |  |
| `GET` | `/v1/auth/login` | `read` |  |
| `POST` | `/v1/auth/logout` | `relaxed` |  |
| `GET` | `/v1/auth/me` | `read` |  |
| `GET` | `/v1/auth/me/photo` | `read` |  |
| `POST` | `/v1/auth/me/photo/refresh` | `strict` | Llama a Microsoft Graph en cada solicitud |
| `POST` | `/v1/auth/refresh` | `relaxed` |  |
| `GET` | `/v1/clientes` | `read` |  |
| `GET` | `/v1/commercial/customers` | `read` |  |
| `GET` | `/v1/commercial/legal-suppliers` | `read` |  |
| `GET` | `/v1/commercial/price-increases` | `read` |  |
| `GET` | `/v1/commercial/price-increases/analysis` | `read` |  |
| `GET` | `/v1/commercial/price-increases/options` | `read` |  |
| `POST` | `/v1/commercial/price-increases/upload` | `relaxed` |  |
| `PATCH` | `/v1/commercial/price-increases/{increase_id}` | `relaxed` |  |
| `GET` | `/v1/commercial/products` | `read` |  |
| `POST` | `/v1/commercial/products/margins` | `relaxed` |  |
| `GET` | `/v1/commercial/products/margins/template` | `read` |  |
| `GET` | `/v1/commercial/products/{sku}` | `read` |  |
| `PATCH` | `/v1/commercial/products/{sku}` | `relaxed` |  |
| `PUT` | `/v1/commercial/products/{sku}/margins` | `relaxed` |  |
| `GET` | `/v1/commercial/suppliers` | `read` |  |
| `GET` | `/v1/commercial/suppliers/{code}` | `read` |  |
| `PATCH` | `/v1/commercial/suppliers/{code}` | `relaxed` |  |
| `GET` | `/v1/directory/admin/employees` | `read` |  |
| `POST` | `/v1/directory/admin/employees` | `relaxed` |  |
| `DELETE` | `/v1/directory/admin/employees/{employee_id}` | `relaxed` |  |
| `GET` | `/v1/directory/admin/employees/{employee_id}` | `read` |  |
| `PATCH` | `/v1/directory/admin/employees/{employee_id}` | `relaxed` |  |
| `GET` | `/v1/directory/admin/sites` | `read` |  |
| `POST` | `/v1/directory/admin/sites` | `relaxed` |  |
| `DELETE` | `/v1/directory/admin/sites/{site_id}` | `relaxed` |  |
| `PATCH` | `/v1/directory/admin/sites/{site_id}` | `relaxed` |  |
| `GET` | `/v1/directory/admin/sync` | `read` |  |
| `POST` | `/v1/directory/admin/sync` | `strict` | Lanza una sincronización completa con Microsoft Graph |
| `GET` | `/v1/directory/admin/sync/runs` | `read` |  |
| `GET` | `/v1/directory/employees` | `read` |  |
| `GET` | `/v1/directory/employees/birthdays` | `read` |  |
| `GET` | `/v1/directory/employees/me` | `read` |  |
| `GET` | `/v1/directory/employees/me/contact-schedule` | `read` |  |
| `PUT` | `/v1/directory/employees/me/contact-schedule` | `relaxed` |  |
| `GET` | `/v1/directory/employees/me/onboarding` | `read` |  |
| `POST` | `/v1/directory/employees/me/onboarding` | `relaxed` |  |
| `GET` | `/v1/directory/employees/new-hires` | `read` |  |
| `GET` | `/v1/directory/employees/{employee_id}` | `read` |  |
| `GET` | `/v1/directory/employees/{employee_id}/contact-schedule` | `read` |  |
| `GET` | `/v1/directory/employees/{employee_id}/photo` | `read` |  |
| `GET` | `/v1/directory/employees/{employee_id}/team` | `read` |  |
| `GET` | `/v1/directory/sites` | `read` |  |
| `GET` | `/v1/documents/search` | `read` |  |
| `DELETE` | `/v1/documents/{document_id}` | `relaxed` |  |
| `GET` | `/v1/documents/{document_id}` | `read` |  |
| `PATCH` | `/v1/documents/{document_id}` | `relaxed` |  |
| `GET` | `/v1/documents/{document_id}/download-url` | `read` |  |
| `GET` | `/v1/dwh-monitoring/extractions` | `read` |  |
| `GET` | `/v1/dwh-monitoring/freshness` | `read` |  |
| `GET` | `/v1/dwh-monitoring/freshness/history` | `read` |  |
| `GET` | `/v1/dwh-monitoring/freshness/tables` | `read` |  |
| `GET` | `/v1/dwh-monitoring/overview` | `read` |  |
| `GET` | `/v1/dwh-monitoring/quality` | `read` |  |
| `GET` | `/v1/dwh-monitoring/schedule` | `read` |  |
| `GET` | `/v1/dwh-monitoring/sequence-runs` | `read` |  |
| `GET` | `/v1/dwh-monitoring/sequence-tables` | `read` |  |
| `GET` | `/v1/dwh-monitoring/sequences` | `read` |  |
| `GET` | `/v1/dwh-monitoring/sequences/{secuencia}` | `read` |  |
| `GET` | `/v1/dwh-monitoring/statistics` | `read` |  |
| `GET` | `/v1/dwh-monitoring/sync` | `read` |  |
| `POST` | `/v1/dwh-monitoring/sync` | `relaxed` |  |
| `GET` | `/v1/dwh-monitoring/transformations` | `read` |  |
| `GET` | `/v1/dwh-publish` | `read` |  |
| `GET` | `/v1/dwh-publish/failed` | `read` |  |
| `POST` | `/v1/dwh-publish/reconcile` | `relaxed` |  |
| `POST` | `/v1/dwh-publish/retry` | `relaxed` |  |
| `GET` | `/v1/jobs` | `read` |  |
| `GET` | `/v1/jobs/runs` | `read` |  |
| `POST` | `/v1/jobs/{key}/run` | `strict` | Lanza una sincronización de maestros contra el datawarehouse |
| `GET` | `/v1/masters` | `read` |  |
| `GET` | `/v1/masters/{slug}` | `read` |  |
| `GET` | `/v1/masters/{slug}/records` | `read` |  |
| `GET` | `/v1/monitoring/board` | `read` |  |
| `GET` | `/v1/notifications` | `read` |  |
| `GET` | `/v1/notifications/count` | `read` |  |
| `POST` | `/v1/notifications/ingest` | `relaxed` |  |
| `GET` | `/v1/notifications/ingest/audiences` | `read` |  |
| `GET` | `/v1/notifications/push/public-key` | `read` |  |
| `DELETE` | `/v1/notifications/push/subscriptions` | `relaxed` |  |
| `POST` | `/v1/notifications/push/subscriptions` | `relaxed` |  |
| `PATCH` | `/v1/notifications/read` | `relaxed` |  |
| `PATCH` | `/v1/notifications/{notification_id}/read` | `relaxed` |  |
| `GET` | `/v1/order-validations` | `read` |  |
| `POST` | `/v1/order-validations` | `relaxed` |  |
| `GET` | `/v1/order-validations/catalogs` | `read` |  |
| `GET` | `/v1/order-validations/permissions` | `read` |  |
| `GET` | `/v1/order-validations/rules` | `read` |  |
| `DELETE` | `/v1/order-validations/{batch_id}` | `relaxed` |  |
| `GET` | `/v1/order-validations/{batch_id}` | `read` |  |
| `POST` | `/v1/order-validations/{batch_id}/approvals` | `relaxed` |  |
| `PUT` | `/v1/order-validations/{batch_id}/margin-threshold` | `relaxed` |  |
| `GET` | `/v1/order-validations/{batch_id}/orders` | `read` |  |
| `DELETE` | `/v1/order-validations/{batch_id}/orders/{document}/approval` | `relaxed` |  |
| `POST` | `/v1/order-validations/{batch_id}/revalidate` | `relaxed` |  |
| `GET` | `/v1/permissions/audit` | `read` |  |
| `GET` | `/v1/permissions/catalog` | `read` |  |
| `GET` | `/v1/permissions/me` | `read` |  |
| `GET` | `/v1/permissions/modules` | `read` |  |
| `GET` | `/v1/permissions/users/{user_id}` | `read` |  |
| `GET` | `/v1/pqrsf/activity` | `read` |  |
| `GET` | `/v1/pqrsf/activity/last-seen` | `read` |  |
| `POST` | `/v1/pqrsf/activity/visits` | `relaxed` |  |
| `GET` | `/v1/pqrsf/assignees` | `read` |  |
| `GET` | `/v1/pqrsf/cases` | `read` |  |
| `POST` | `/v1/pqrsf/cases` | `relaxed` |  |
| `GET` | `/v1/pqrsf/cases/export` | `strict` | Arma una hoja de cálculo de hasta 20.000 filas |
| `GET` | `/v1/pqrsf/cases/suggestions` | `read` |  |
| `GET` | `/v1/pqrsf/cases/summary` | `read` |  |
| `DELETE` | `/v1/pqrsf/cases/{case_id}` | `relaxed` |  |
| `GET` | `/v1/pqrsf/cases/{case_id}` | `read` |  |
| `PATCH` | `/v1/pqrsf/cases/{case_id}` | `relaxed` |  |
| `POST` | `/v1/pqrsf/cases/{case_id}/assignment` | `relaxed` |  |
| `GET` | `/v1/pqrsf/cases/{case_id}/attachments/{attachment_id}/download` | `read` |  |
| `GET` | `/v1/pqrsf/cases/{case_id}/client-replies` | `read` |  |
| `POST` | `/v1/pqrsf/cases/{case_id}/client-replies/{notification_id}/retry` | `relaxed` |  |
| `POST` | `/v1/pqrsf/cases/{case_id}/comments` | `relaxed` |  |
| `GET` | `/v1/pqrsf/cases/{case_id}/deliveries` | `read` |  |
| `GET` | `/v1/pqrsf/cases/{case_id}/documents/search` | `read` |  |
| `POST` | `/v1/pqrsf/cases/{case_id}/events/{event_id}/forward-to-customer` | `relaxed` |  |
| `POST` | `/v1/pqrsf/cases/{case_id}/events/{event_id}/forward-to-customer/preview` | `relaxed` |  |
| `GET` | `/v1/pqrsf/cases/{case_id}/files` | `read` |  |
| `GET` | `/v1/pqrsf/cases/{case_id}/files/{file_id}/download` | `read` |  |
| `POST` | `/v1/pqrsf/cases/{case_id}/items` | `relaxed` |  |
| `DELETE` | `/v1/pqrsf/cases/{case_id}/items/{item_id}` | `relaxed` |  |
| `PATCH` | `/v1/pqrsf/cases/{case_id}/items/{item_id}` | `relaxed` |  |
| `POST` | `/v1/pqrsf/cases/{case_id}/items/{item_id}/delivery` | `relaxed` |  |
| `POST` | `/v1/pqrsf/cases/{case_id}/read` | `relaxed` |  |
| `POST` | `/v1/pqrsf/cases/{case_id}/status` | `relaxed` |  |
| `GET` | `/v1/pqrsf/cases/{case_id}/supplier-messages` | `read` |  |
| `POST` | `/v1/pqrsf/cases/{case_id}/supplier-messages` | `relaxed` |  |
| `POST` | `/v1/pqrsf/cases/{case_id}/supplier-messages/{notification_id}/retry` | `relaxed` |  |
| `PATCH` | `/v1/pqrsf/causes/{cause_id}` | `relaxed` |  |
| `GET` | `/v1/pqrsf/certificates` | `read` |  |
| `GET` | `/v1/pqrsf/certificates/documents` | `read` |  |
| `DELETE` | `/v1/pqrsf/certificates/{document_type_id}` | `relaxed` |  |
| `PUT` | `/v1/pqrsf/certificates/{document_type_id}` | `relaxed` |  |
| `POST` | `/v1/pqrsf/dependencies` | `relaxed` |  |
| `PATCH` | `/v1/pqrsf/dependencies/{dependency_id}` | `relaxed` |  |
| `GET` | `/v1/pqrsf/document-deliveries` | `read` |  |
| `GET` | `/v1/pqrsf/document-deliveries/summary` | `read` |  |
| `GET` | `/v1/pqrsf/document-deliveries/{delivery_id}` | `read` |  |
| `POST` | `/v1/pqrsf/document-deliveries/{delivery_id}/retry` | `relaxed` |  |
| `POST` | `/v1/pqrsf/document-types` | `relaxed` |  |
| `PATCH` | `/v1/pqrsf/document-types/{document_type_id}` | `relaxed` |  |
| `GET` | `/v1/pqrsf/feedback` | `read` |  |
| `GET` | `/v1/pqrsf/feedback/summary` | `read` |  |
| `GET` | `/v1/pqrsf/feedback/{feedback_id}` | `read` |  |
| `GET` | `/v1/pqrsf/feedback/{feedback_id}/files/{file_id}/download` | `read` |  |
| `GET` | `/v1/pqrsf/people` | `read` |  |
| `GET` | `/v1/pqrsf/request-types` | `read` |  |
| `PATCH` | `/v1/pqrsf/request-types/{request_type_id}` | `relaxed` |  |
| `GET` | `/v1/pqrsf/requests` | `read` |  |
| `GET` | `/v1/pqrsf/requests/{request_id}` | `read` |  |
| `GET` | `/v1/pqrsf/requests/{request_id}/attachments/{attachment_id}/download` | `read` |  |
| `POST` | `/v1/pqrsf/requests/{request_id}/case` | `relaxed` |  |
| `GET` | `/v1/pqrsf/settings/notifications` | `read` |  |
| `PUT` | `/v1/pqrsf/settings/notifications` | `relaxed` |  |
| `POST` | `/v1/pqrsf/statuses` | `relaxed` |  |
| `PATCH` | `/v1/pqrsf/statuses/{status_id}` | `relaxed` |  |
| `GET` | `/v1/pqrsf/subjects` | `read` |  |
| `POST` | `/v1/pqrsf/subjects` | `relaxed` |  |
| `PATCH` | `/v1/pqrsf/subjects/{subject_id}` | `relaxed` |  |
| `POST` | `/v1/pqrsf/subjects/{subject_id}/causes` | `relaxed` |  |
| `GET` | `/v1/pqrsf/supplier-contacts` | `read` |  |
| `GET` | `/v1/pqrsf/tracking/catalogs` | `read` |  |
| `GET` | `/v1/price-list-updates/liquor-taxes` | `read` |  |
| `GET` | `/v1/price-list-updates/liquor-taxes/updates` | `read` |  |
| `POST` | `/v1/price-list-updates/liquor-taxes/updates` | `relaxed` |  |
| `POST` | `/v1/price-list-updates/liquor-taxes/updates/{update_id}/cancel` | `relaxed` |  |
| `GET` | `/v1/price-list-updates/liquor-taxes/updates/{update_id}/file.{fmt}` | `read` |  |
| `POST` | `/v1/price-list-updates/liquor-taxes/updates/{update_id}/uploaded` | `relaxed` |  |
| `GET` | `/v1/price-list-updates/loads` | `read` |  |
| `GET` | `/v1/price-list-updates/main` | `read` |  |
| `POST` | `/v1/price-list-updates/main/apply` | `relaxed` |  |
| `GET` | `/v1/price-list-updates/main/keys` | `read` |  |
| `PUT` | `/v1/price-list-updates/main/note` | `relaxed` |  |
| `PUT` | `/v1/price-list-updates/main/prices` | `relaxed` |  |
| `PUT` | `/v1/price-list-updates/main/review` | `relaxed` |  |
| `GET` | `/v1/price-list-updates/main/rows` | `read` |  |
| `POST` | `/v1/price-list-updates/main/sync` | `relaxed` |  |
| `GET` | `/v1/price-list-updates/staging` | `read` |  |
| `GET` | `/v1/price-list-updates/staging/{price_list_id}/file.{fmt}` | `read` |  |
| `POST` | `/v1/price-list-updates/staging/{price_list_id}/loads` | `relaxed` |  |
| `GET` | `/v1/price-list-updates/{quotation_id}/lines/{sku}/insight` | `read` |  |
| `PUT` | `/v1/price-list-updates/{quotation_id}/lines/{sku}/note` | `relaxed` |  |
| `PUT` | `/v1/price-list-updates/{quotation_id}/review` | `relaxed` |  |
| `GET` | `/v1/price-list-updates/{quotation_id}/stock` | `read` |  |
| `GET` | `/v1/public/pqrsf/catalogs` | `read` |  |
| `GET` | `/v1/public/pqrsf/products` | `read` |  |
| `POST` | `/v1/public/pqrsf/requests` | `strict` | Abierto a cualquiera: además tamaño, tope global y CAPTCHA (public_guard) |
| `GET` | `/v1/quotations` | `read` |  |
| `POST` | `/v1/quotations` | `relaxed` |  |
| `GET` | `/v1/quotations/billing-centers` | `read` |  |
| `GET` | `/v1/quotations/customers` | `read` |  |
| `POST` | `/v1/quotations/list-updates` | `relaxed` |  |
| `GET` | `/v1/quotations/permissions` | `read` |  |
| `GET` | `/v1/quotations/price-lists` | `read` |  |
| `GET` | `/v1/quotations/rules` | `read` |  |
| `DELETE` | `/v1/quotations/{quotation_id}` | `relaxed` |  |
| `GET` | `/v1/quotations/{quotation_id}` | `read` |  |
| `PATCH` | `/v1/quotations/{quotation_id}/billing-center` | `relaxed` |  |
| `POST` | `/v1/quotations/{quotation_id}/confirm` | `relaxed` |  |
| `GET` | `/v1/quotations/{quotation_id}/cost-centers/compare` | `read` |  |
| `GET` | `/v1/quotations/{quotation_id}/export.txt` | `read` |  |
| `GET` | `/v1/quotations/{quotation_id}/export.xlsx` | `read` |  |
| `POST` | `/v1/quotations/{quotation_id}/lines` | `relaxed` |  |
| `PUT` | `/v1/quotations/{quotation_id}/lines` | `relaxed` |  |
| `GET` | `/v1/quotations/{quotation_id}/lines.xlsx` | `read` |  |
| `DELETE` | `/v1/quotations/{quotation_id}/lines/{sku}` | `relaxed` |  |
| `POST` | `/v1/quotations/{quotation_id}/local-lines` | `relaxed` |  |
| `PATCH` | `/v1/quotations/{quotation_id}/offer` | `relaxed` |  |
| `GET` | `/v1/quotations/{quotation_id}/offer.xlsx` | `read` |  |
| `POST` | `/v1/quotations/{quotation_id}/refresh` | `relaxed` |  |
| `POST` | `/v1/quotations/{quotation_id}/return` | `relaxed` |  |
| `POST` | `/v1/quotations/{quotation_id}/uploaded` | `relaxed` |  |
| `POST` | `/v1/quotations/{quotation_id}/validate` | `relaxed` |  |
| `GET` | `/v1/roles` | `read` |  |
| `GET` | `/v1/salespeople/links` | `read` |  |
| `POST` | `/v1/salespeople/links` | `relaxed` |  |
| `DELETE` | `/v1/salespeople/links/{link_id}` | `relaxed` |  |
| `GET` | `/v1/salespeople/me` | `read` |  |
| `GET` | `/v1/salespeople/roster` | `read` |  |
| `POST` | `/v1/salespeople/sync` | `relaxed` |  |
| `GET` | `/v1/sites` | `read` |  |
| `POST` | `/v1/sites` | `relaxed` |  |
| `DELETE` | `/v1/sites/{site_id}` | `relaxed` |  |
| `GET` | `/v1/sites/{site_id}` | `read` |  |
| `PATCH` | `/v1/sites/{site_id}` | `relaxed` |  |
| `GET` | `/v1/supplier-special-cases` | `read` |  |
| `POST` | `/v1/supplier-special-cases` | `relaxed` |  |
| `GET` | `/v1/supplier-special-cases/{case_id}` | `read` |  |
| `PATCH` | `/v1/supplier-special-cases/{case_id}` | `relaxed` |  |
| `GET` | `/v1/supplier-support-profiles` | `read` |  |
| `DELETE` | `/v1/supplier-support-profiles/{supplier_code}` | `relaxed` |  |
| `GET` | `/v1/supplier-support-profiles/{supplier_code}` | `read` |  |
| `PUT` | `/v1/supplier-support-profiles/{supplier_code}` | `relaxed` |  |
| `GET` | `/v1/supplier-support-requests` | `read` |  |
| `GET` | `/v1/supplier-support-requests/{request_id}` | `read` |  |
| `POST` | `/v1/supplier-support-requests/{request_id}/approve` | `relaxed` |  |
| `POST` | `/v1/supplier-support-requests/{request_id}/reject` | `relaxed` |  |
| `POST` | `/v1/supplier-supports` | `relaxed` |  |
| `POST` | `/v1/supplier-supports/attachments` | `relaxed` |  |
| `POST` | `/v1/supplier-supports/attachments/preview` | `relaxed` |  |
| `POST` | `/v1/supplier-supports/batch` | `relaxed` |  |
| `POST` | `/v1/supplier-supports/bulk` | `relaxed` |  |
| `GET` | `/v1/supplier-supports/bulk/template` | `read` |  |
| `GET` | `/v1/supplier-supports/catalogs` | `read` |  |
| `GET` | `/v1/supplier-supports/form-schema` | `read` |  |
| `GET` | `/v1/supplier-supports/permissions` | `read` |  |
| `POST` | `/v1/supplier-supports/requests/batch` | `relaxed` |  |
| `POST` | `/v1/supplier-supports/search` | `relaxed` |  |
| `GET` | `/v1/supplier-supports/search/fields` | `read` |  |
| `GET` | `/v1/supplier-supports/{support_id}` | `read` |  |
| `PATCH` | `/v1/supplier-supports/{support_id}` | `relaxed` |  |
| `GET` | `/v1/supplier-supports/{support_id}/attachments` | `read` |  |
| `POST` | `/v1/supplier-supports/{support_id}/attachments` | `relaxed` |  |
| `DELETE` | `/v1/supplier-supports/{support_id}/attachments/{document_id}` | `relaxed` |  |
| `GET` | `/v1/supplier-supports/{support_id}/attachments/{document_id}/view` | `read` |  |
| `GET` | `/v1/supplier-supports/{support_id}/conflicts` | `read` |  |
| `GET` | `/v1/supplier-supports/{support_id}/jdh-history` | `read` |  |
| `DELETE` | `/v1/supplier-supports/{support_id}/jdh-support` | `relaxed` |  |
| `POST` | `/v1/supplier-supports/{support_id}/requests` | `relaxed` |  |
| `POST` | `/v1/supplier-supports/{support_id}/void` | `relaxed` |  |
| `GET` | `/v1/users` | `read` |  |
| `GET` | `/v1/users/me` | `read` |  |
| `GET` | `/v1/users/{user_id}` | `read` |  |
| `PATCH` | `/v1/users/{user_id}` | `relaxed` |  |
<!-- niveles:fin -->

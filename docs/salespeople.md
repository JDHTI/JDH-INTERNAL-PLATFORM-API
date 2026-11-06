# Vendedores y alcance comercial

Vincula a cada usuario de la plataforma con su código de vendedor o de supervisor de SIESA, y de
ahí decide qué clientes puede cotizar. El servidor aplica el alcance; el cliente no
lo elige. Módulo: `app/modules/salespeople`.

## Fuente

`consumo.vendedores` del DWH:

- `codigo`: el vendedor.
- `correo`: su correo corporativo. Solo lo tiene una minoría (111 de 587 activos, noviembre 2026).
- `cod_supervisor` y `supervisor`: su supervisor. Es un catálogo aparte: ningún código de
  supervisor es código de vendedor y ninguno tiene correo.

## Vínculos (`salesperson_links`)

Una fila por `(kind, code)`, con `kind` `seller` o `supervisor`. Un código es de una persona como
máximo; una persona puede tener varios.

| `source` | Qué es |
| --- | --- |
| `auto` | Por correo: `POST /salespeople/sync` guarda un vínculo por cada vendedor activo cuyo correo es de un usuario activo |
| `manual` | Lo hizo un administrador en el panel. Gana sobre el correo |
| `excluded` | Un administrador lo desvinculó. Sin usuario: el correo no lo vuelve a vincular solo |

Mientras una persona no tenga vínculos de vendedor guardados, su correo la vincula en vivo, salvo
a códigos que ya tiene otra persona o que fueron desvinculados. Los supervisores solo se vinculan
a mano.

## Alcance

| Alcance | Quién | Qué cotiza |
| --- | --- | --- |
| `all` | Admin, owner, moderador; todo miembro o responsable de BI; líder o responsable de Comercial | Todos los clientes |
| `team` | Vinculado a uno o más códigos de supervisor | Clientes de los vendedores activos de su equipo, más sus propios códigos |
| `mine` | Vinculado a uno o más códigos de vendedor | Clientes de sus códigos |
| `none` | Cualquier otro, también miembros de Comercial sin vínculo | Nada hasta que TI lo vincule |

BI y Comercial salen de `QUOTATIONS_APPROVER_AREA_SLUG` y `QUOTATIONS_MANAGER_AREA_SLUG`.

## Dónde se aplica (cotizador)

- `GET /quotations/customers`: con alcance `team`, `mine` o `none` se ignora `scope` y se
  devuelve la cartera. Con `all` la persona sigue eligiendo `mine` o `all`.
- `POST /quotations`: 403 si ninguna sucursal del NIT es de sus códigos.
- Qué cotizaciones ve cada uno ya no depende del alcance: ver `docs/cotizador.md` (quien no
  valida ve solo las que creó).
- `GET /quotations/permissions` trae `sales_scope`.

Las solicitudes de apoyo no se tocan: no tienen cliente ni vendedor, siguen por quien las pide.

## Endpoints (`/salespeople`)

| Método y ruta | Quién | Uso |
| --- | --- | --- |
| `GET /me` | autenticado | `{scope, seller_codes, supervisor_codes, codes}`; `codes` `null` es todo |
| `GET /roster?kind=seller\|supervisor&q=&linked=&active_only=&page=&size=` | admin o moderador | Vendedores o supervisores con quién tiene cada código (`holder_email`, `link`) |
| `GET /links?email=` | admin o moderador | Usuario y sus vínculos |
| `POST /links` `{email, kind, code}` | admin | Vincula a mano. 404 si el código no está en SIESA o la persona no existe; 409 si otra persona lo tiene a mano |
| `DELETE /links/{id}` | admin | Desvincula (queda `excluded`) |
| `POST /sync` | admin | Vincula por correo: `{created, matched, without_user}` |

La bitácora de permisos registra `salesperson_linked`, `salesperson_unlinked` y
`salespeople_synced`.

## Despliegue

Migración `0045_salesperson_links`. Después, `POST /salespeople/sync` una vez (o desde el panel)
para guardar los 111 vínculos por correo; sin eso el correo igual los vincula en vivo.

"""PQRSF submission and administrator query use cases."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.pqrsf.application.document_delivery import DocumentDeliveryService
from app.modules.pqrsf.application.feedback import FeedbackService
from app.modules.pqrsf.domain.entities import (
    HANDLING_ACKNOWLEDGE,
    PICKUP_ORDER_ANSWERS,
    AreaRef,
    Cause,
    ClaimedProduct,
    CreateResult,
    DocumentCategory,
    ImageUpload,
    PqrsfAttachment,
    PqrsfFilters,
    PqrsfPage,
    PqrsfRequest,
    ProductInput,
    ProductMaster,
    ProductOption,
    RequestedDocument,
    RequestType,
    SanitizedImage,
    Subject,
    clean_optional,
    clean_required,
    storage_key_for,
    validate_idempotency_key,
)
from app.modules.pqrsf.domain.feedback import ORIGIN_FORM, FeedbackFile
from app.modules.pqrsf.domain.ports import (
    BinaryStorage,
    CaseOpener,
    ImageProcessor,
    MalwareScanner,
    PqrsfRepository,
    ProductCatalog,
    VideoInspector,
)
from app.modules.pqrsf.domain.tracking import Dependency
from app.modules.sites.application.service import SiteService

logger = logging.getLogger(__name__)

# Documents one public request can ask for; a case itself admits many more.
_MAX_DOCUMENTS = 30
# Product lines of one complaint or claim, and the most units one line can claim.
_MAX_PRODUCTS = 20
_MAX_AFFECTED_QUANTITY = 1_000_000
# Most products one search of the public form returns.
_MAX_PRODUCT_RESULTS = 20


@dataclass(frozen=True)
class _Classification:
    """The validated subject, cause, description and product lines of a request."""

    subject: str | None
    description: str | None
    subject_ref: Subject | None = None
    cause_ref: Cause | None = None
    products: list[ClaimedProduct] = field(default_factory=list)
    pickup_order_requested: str | None = None
    pickup_order_number: str | None = None


class PqrsfService:
    """Coordinate the public form and protected administrator views."""

    def __init__(
        self,
        repository: PqrsfRepository,
        sites: SiteService,
        storage: BinaryStorage | None = None,
        image_processor: ImageProcessor | None = None,
        malware_scanner: MalwareScanner | None = None,
        cases: CaseOpener | None = None,
        max_attachments: int = 5,
        max_attachment_bytes: int = 5 * 1024 * 1024,
        max_total_attachment_bytes: int = 25 * 1024 * 1024,
        feedback: FeedbackService | None = None,
        deliveries: DocumentDeliveryService | None = None,
        products: ProductCatalog | None = None,
        video_inspector: VideoInspector | None = None,
        max_videos: int = 1,
        max_video_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        """Create the service.

        Args:
            repository (PqrsfRepository): PQRSF persistence port.
            sites (SiteService): Site catalog use cases.
            storage (BinaryStorage | None): Private object storage adapter.
            image_processor (ImageProcessor | None): Image sanitation adapter.
            malware_scanner (MalwareScanner | None): Antivirus adapter.
            cases (CaseOpener | None): Opens the follow-up case of new requests.
            max_attachments (int): Maximum number of images per request.
            max_attachment_bytes (int): Maximum bytes per image.
            max_total_attachment_bytes (int): Maximum bytes across images.
            feedback (FeedbackService | None): Stores and thanks suggestions and
                compliments; without it every type opens a case.
            deliveries (DocumentDeliveryService | None): Answers document requests with the
                configured certificates; without it every request opens a case.
            products (ProductCatalog | None): Product master used to search references and
                to resolve their name and supplier; without it references are not checked.
            video_inspector (VideoInspector | None): Checks evidence videos; without it
                videos are rejected.
            max_videos (int): Maximum number of videos per request.
            max_video_bytes (int): Maximum bytes per video.
        """

        self.products = products
        self.video_inspector = video_inspector
        self.max_videos = max_videos
        self.max_video_bytes = max_video_bytes
        self.deliveries = deliveries
        self.feedback = feedback
        self.repository = repository
        self.sites = sites
        self.storage = storage
        self.image_processor = image_processor
        self.malware_scanner = malware_scanner
        self.cases = cases
        self.max_attachments = max_attachments
        self.max_attachment_bytes = max_attachment_bytes
        self.max_total_attachment_bytes = max_total_attachment_bytes

    async def catalog(self) -> tuple[list, list, list]:
        """Return active request types, sites and document categories for the public form."""

        return (
            await self.repository.list_request_types(),
            await self.sites.list_sites(),
            await self._with_automatic(await self.repository.list_document_categories()),
        )

    async def subjects(self) -> list[Subject]:
        """Return the active subjects, with their causes, offered on the public form.

        Returns:
            list[Subject]: Subjects in display order; each says which types offer it.
        """

        return await self.repository.list_subjects()

    async def areas(self) -> list[Dependency]:
        """Return the areas a requester can pick on the public form.

        The area narrows the subjects shown and is stored with the request; it decides who
        attends the case only when no cause, subject or document category does.

        Returns:
            list[Dependency]: Active dependencies in display order.
        """

        return await self.repository.list_areas()

    async def search_products(self, query: str, limit: int = 10) -> list[ProductOption]:
        """Find products by reference or name for the product lines of the form.

        Args:
            query (str): Reference or part of the name; at least 2 characters.
            limit (int): Most results to return (1 to 20).

        Returns:
            list[ProductOption]: Matching active products, without supplier data.
        """

        term = " ".join(query.split())
        if len(term) < 2:
            raise InvalidInputError("Escribe al menos 2 caracteres de la referencia o el nombre")
        if self.products is None:
            raise DependencyUnavailableError("El maestro de productos no está configurado")
        return await self.products.search(term, max(1, min(limit, _MAX_PRODUCT_RESULTS)))

    async def _with_automatic(self, categories: list[DocumentCategory]) -> list[DocumentCategory]:
        """Mark the documents a certificate answers at once, so the form can say so."""

        if self.deliveries is None:
            return categories
        automatic = await self.deliveries.automatic_documents(
            [option.id for category in categories for option in category.documents]
        )
        return [
            replace(
                category,
                documents=[replace(o, automatic=o.id in automatic) for o in category.documents],
            )
            for category in categories
        ]

    async def create_request(
        self,
        *,
        request_type_id: UUID,
        site_id: UUID,
        full_name: str,
        business_name: str,
        nit: str,
        address: str,
        department: str,
        city: str,
        email: str,
        idempotency_key: str,
        idempotency_payload: Mapping[str, object],
        subject: str | None = None,
        description: str | None = None,
        images: Sequence[ImageUpload] = (),
        document_request: bool = False,
        document_category_id: UUID | None = None,
        document_type_ids: Sequence[UUID] = (),
        document_other: str | None = None,
        subject_id: UUID | None = None,
        cause_id: UUID | None = None,
        products: Sequence[ProductInput] = (),
        pickup_order_requested: str | None = None,
        pickup_order_number: str | None = None,
        videos: Sequence[ImageUpload] = (),
        area_id: UUID | None = None,
    ) -> CreateResult:
        """Validate and persist an idempotent public request.

        A type that offers subjects (complaints, claims, petitions that are not for
        documents) is classified by picking a subject and, when it has them, a cause; the
        description is then optional unless one of them is "Otro". Any other request
        keeps a typed subject and a required description (a document request only the
        subject).

        Args:
            request_type_id (UUID): Fixed request classification.
            site_id (UUID): Active submission site.
            full_name (str): Required requester name.
            business_name (str): Required legal/business name.
            nit (str): Required taxpayer identifier.
            address (str): Required address.
            department (str): Required department text.
            city (str): Required city text.
            email (str): Required requester email.
            idempotency_key (str): UUID used to make retries safe.
            idempotency_payload (Mapping[str, object]): Canonical hash input.
            subject (str | None): Typed one-line subject: required where no subject is
                picked from the catalog; optional detail of a subject "Otro".
            description (str | None): Request detail (see the rules above).
            images (Sequence[ImageUpload]): Optional public image uploads; not
                accepted on a document request.
            document_request (bool): Whether the customer asks for documents.
            document_category_id (UUID | None): Category of the documents asked for.
            document_type_ids (Sequence[UUID]): Catalog documents asked for.
            document_other (str | None): Free-text document not in the catalog.
            subject_id (UUID | None): Subject picked from the catalog.
            cause_id (UUID | None): Cause of that subject.
            products (Sequence[ProductInput]): Product lines, where the cause admits them.
            pickup_order_requested (str | None): ``yes``, ``no`` or ``unknown``; required
                with products.
            pickup_order_number (str | None): Pickup order number, only with ``yes``.
            videos (Sequence[ImageUpload]): Evidence videos, where images are accepted.
            area_id (UUID | None): Area the requester picked; required where the type
                ``asks_area`` and refused otherwise.

        Returns:
            CreateResult: The stored request and whether it was replayed.
        """

        result = await self._store_request(
            request_type_id=request_type_id,
            site_id=site_id,
            full_name=full_name,
            business_name=business_name,
            nit=nit,
            address=address,
            department=department,
            city=city,
            email=email,
            subject=subject,
            description=description,
            idempotency_key=idempotency_key,
            idempotency_payload=idempotency_payload,
            images=images,
            document_request=document_request,
            document_category_id=document_category_id,
            document_type_ids=document_type_ids,
            document_other=document_other,
            subject_id=subject_id,
            cause_id=cause_id,
            products=products,
            pickup_order_requested=pickup_order_requested,
            pickup_order_number=pickup_order_number,
            videos=videos,
            area_id=area_id,
        )
        return await self._with_case(result)

    async def _with_case(self, result: CreateResult) -> CreateResult:
        """Open (or find) the follow-up case without failing the submission.

        The request is already stored; a failure here is logged and the case
        can be opened later with the recovery endpoint.
        """

        if (
            self.feedback is not None
            and result.request.request_type.handling == HANDLING_ACKNOWLEDGE
        ):
            await self._thank(result.request)
            return replace(result, handled_as="feedback")
        if self.deliveries is not None and result.request.document_request:
            answered = await self._answer_documents(result)
            if answered is not None:
                return answered
        if self.cases is None:
            return result
        try:
            if result.replayed:
                code = await self.cases.code_for_request(result.request.id)
                if code is None:
                    code = (await self.cases.open_for_request(result.request)).code
            else:
                code = (await self.cases.open_for_request(result.request)).code
        except Exception:
            logger.exception("Could not open the follow-up case of PQRSF %s", result.request.id)
            return result
        return replace(result, tracking_code=code)

    async def _answer_documents(self, result: CreateResult) -> CreateResult | None:
        """Send the certificates that are configured and open a case for the rest.

        Returns ``None`` when nothing of the request is answered automatically (or anything
        fails before something was sent): the request then opens its case as usual.

        Order matters: the delivery is logged first, the case for what a person answers
        is opened second, and the emails are queued last so the first one can name that
        case. Every step is idempotent, so a replayed submission completes what was left.
        """

        assert self.deliveries is not None
        request = result.request
        try:
            plan = await self.deliveries.plan(request)
            if not plan.automatic or (plan.manual and self.cases is None):
                return None
            delivery = await self.deliveries.record(request, plan)
        except Exception:
            logger.exception("Could not answer the documents of PQRSF %s", request.id)
            return None
        code = delivery.case_code
        if plan.manual and self.cases is not None and code is None:
            try:
                code = (
                    await self.cases.open_for_request(replace(request, documents=plan.manual))
                ).code
                await self.deliveries.set_case(delivery, code)
            except Exception:
                logger.exception("Could not open the case of PQRSF %s", request.id)
        try:
            await self.deliveries.send(delivery)
        except Exception:
            logger.exception("Could not queue the certificates of PQRSF %s", request.id)
        return replace(
            result,
            tracking_code=code,
            handled_as="case" if plan.manual else "documents",
            delivered=[item.name for item in delivery.items],
        )

    async def _thank(self, request: PqrsfRequest) -> None:
        """Store a suggestion or compliment apart from the cases and thank its sender.

        The request is already stored; a failure here is logged and never fails the
        submission. Each step is idempotent, so a replayed submission completes it.
        """

        assert self.feedback is not None
        try:
            feedback = await self.feedback.record(
                request_type=request.request_type,
                origin=ORIGIN_FORM,
                full_name=request.full_name,
                email=request.email,
                body=request.description,
                subject=request.subject,
                business_name=request.business_name,
                site=request.site,
                request_id=request.id,
                area=request.area,
            )
            if not feedback.files_count:
                # The images stay where the form stored them; the message points at them.
                await self.feedback.add_files(
                    [
                        FeedbackFile(
                            id=uuid4(),
                            feedback_id=feedback.id,
                            original_filename=a.original_filename,
                            extension=a.extension,
                            content_type=a.content_type,
                            size_bytes=a.size_bytes,
                            checksum_sha256=a.checksum_sha256,
                            storage_key=a.storage_key,
                            created_at=a.created_at,
                        )
                        for a in request.attachments
                    ]
                )
            await self.feedback.acknowledge(feedback)
        except Exception:
            logger.exception("Could not record the message of PQRSF %s", request.id)

    async def open_case(self, request_id: UUID) -> str:
        """Open the follow-up case of a stored request that has none.

        Args:
            request_id (UUID): Public request identifier.

        Returns:
            str: The case consecutive.
        """

        if self.cases is None:
            raise DependencyUnavailableError("El seguimiento de casos no está configurado")
        request = await self.get_request(request_id)
        if self.deliveries is not None and request.document_request:
            # What was already sent automatically is not asked of a person again.
            plan = await self.deliveries.plan(request)
            if plan.automatic:
                if not plan.manual:
                    raise ConflictError(
                        "La solicitud ya se respondió automáticamente y no tiene casos"
                    )
                request = replace(request, documents=plan.manual)
        return (await self.cases.open_for_request(request)).code

    async def _store_request(
        self,
        *,
        request_type_id: UUID,
        site_id: UUID,
        full_name: str,
        business_name: str,
        nit: str,
        address: str,
        department: str,
        city: str,
        email: str,
        subject: str | None,
        description: str | None,
        idempotency_key: str,
        idempotency_payload: Mapping[str, object],
        images: Sequence[ImageUpload],
        document_request: bool,
        document_category_id: UUID | None,
        document_type_ids: Sequence[UUID],
        document_other: str | None,
        subject_id: UUID | None,
        cause_id: UUID | None,
        products: Sequence[ProductInput],
        pickup_order_requested: str | None,
        pickup_order_number: str | None,
        videos: Sequence[ImageUpload] = (),
        area_id: UUID | None = None,
    ) -> CreateResult:
        key = validate_idempotency_key(idempotency_key)
        request_type = await self.repository.get_request_type(request_type_id)
        if request_type is None or not request_type.is_active:
            raise NotFoundError("Tipo de solicitud no encontrado")
        site = await self.sites.get_site(site_id)
        if not site.is_active:
            raise InvalidInputError("La sede seleccionada no está disponible")

        area = await self._area(request_type, area_id)
        classification = await self._classify(
            request_type,
            document_request,
            subject_id,
            cause_id,
            subject,
            description,
            products,
            pickup_order_requested,
            pickup_order_number,
        )
        documents = await self._requested_documents(
            request_type,
            [*images, *videos],
            document_request,
            document_category_id,
            document_type_ids,
            document_other,
        )

        processed_images = await self._process_images(request_type.allows_attachments, images)
        processed_images += await self._process_videos(request_type.allows_attachments, videos)
        payload = dict(idempotency_payload)
        payload["images"] = [
            {
                "checksum_sha256": checksum,
                "size_bytes": len(image.content),
                "extension": image.extension,
            }
            for image, checksum in processed_images
        ]
        payload_hash = _payload_hash(payload)
        previous = await self.repository.get_by_idempotency_key(key)
        if previous is not None:
            if previous.idempotency_hash != payload_hash:
                raise ConflictError("Idempotency-Key ya fue usado con otros datos")
            return CreateResult(previous, replayed=True)

        request_id = uuid4()
        request = PqrsfRequest(
            id=request_id,
            reference=f"PQRSF-{request_id}",
            request_type=request_type,
            site=site,
            full_name=clean_required(full_name, "nombre completo", 200),
            business_name=clean_required(business_name, "razón social", 200),
            nit=clean_required(nit, "NIT", 30),
            address=clean_required(address, "dirección", 300),
            department=clean_required(department, "departamento", 100),
            city=clean_required(city, "ciudad", 100),
            email=clean_required(email, "correo", 320).lower(),
            subject=classification.subject,
            description=classification.description,
            subject_ref=classification.subject_ref,
            cause_ref=classification.cause_ref,
            products=classification.products,
            pickup_order_requested=classification.pickup_order_requested,
            pickup_order_number=classification.pickup_order_number,
            area=area,
            idempotency_key=key,
            idempotency_hash=payload_hash,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            document_request=document_request,
            document_category_id=document_category_id if document_request else None,
            documents=documents,
        )
        try:
            stored = await self.repository.create_request(request)
        except ConflictError:
            previous = await self.repository.get_by_idempotency_key(key)
            if previous is not None and previous.idempotency_hash == payload_hash:
                return CreateResult(previous, replayed=True)
            raise

        if not processed_images:
            return CreateResult(stored, replayed=False)

        stored_objects: list[str] = []
        attachments: list[PqrsfAttachment] = []
        try:
            for image, image_checksum in processed_images:
                attachment_id = uuid4()
                key = storage_key_for(stored.id, attachment_id, image.extension)
                if self.storage is None:
                    raise DependencyUnavailableError(
                        "El almacenamiento de imágenes no está configurado"
                    )
                await self.storage.put_object(key, image.content, image.content_type)
                stored_objects.append(key)
                attachments.append(
                    PqrsfAttachment(
                        id=attachment_id,
                        request_id=stored.id,
                        original_filename=image.original_filename,
                        extension=image.extension,
                        content_type=image.content_type,
                        size_bytes=len(image.content),
                        checksum_sha256=image_checksum,
                        storage_key=key,
                        created_at=datetime.now(UTC),
                    )
                )
            await self.repository.add_attachments(attachments)
        except Exception as exc:
            for key in stored_objects:
                try:
                    if self.storage is not None:
                        await self.storage.delete_object(key)
                except Exception:
                    pass
            try:
                await self.repository.delete_request(stored.id)
            except Exception:
                pass
            if isinstance(exc, (InvalidInputError, DependencyUnavailableError)):
                raise
            raise DependencyUnavailableError("No se pudieron guardar las imágenes") from exc
        stored.attachments = attachments
        return CreateResult(stored, replayed=False)

    async def _area(self, request_type: RequestType, area_id: UUID | None) -> AreaRef | None:
        """Validate the area picked: required where the type asks for it, else not sent."""

        if not request_type.asks_area:
            if area_id is not None:
                raise InvalidInputError("Este tipo de solicitud no pide área")
            return None
        if area_id is None:
            raise InvalidInputError("Selecciona el área")
        area = next(
            (item for item in await self.repository.list_areas() if item.id == area_id), None
        )
        if area is None:
            raise InvalidInputError("El área seleccionada no está disponible")
        return AreaRef(id=area.id, code=area.code, name=area.name)

    async def _classify(
        self,
        request_type: RequestType,
        document_request: bool,
        subject_id: UUID | None,
        cause_id: UUID | None,
        subject: str | None,
        description: str | None,
        products: Sequence[ProductInput],
        pickup_order_requested: str | None,
        pickup_order_number: str | None,
    ) -> _Classification:
        """Validate the subject, cause, description and product lines of a request."""

        subject_text = clean_optional(subject, "asunto", 200)
        description_text = clean_optional(description, "descripción", 10_000)
        offered = (
            []
            if document_request
            else [
                s for s in await self.repository.list_subjects() if s.offered_to(request_type.code)
            ]
        )
        if not offered:
            if subject_id is not None or cause_id is not None:
                raise InvalidInputError("El asunto y la causal no aplican a esta solicitud")
            if products or pickup_order_requested or pickup_order_number:
                raise InvalidInputError("Esta solicitud no admite productos")
            if subject_text is None:
                raise InvalidInputError("El campo asunto no puede estar vacío")
            if description_text is None and not document_request:
                raise InvalidInputError("El campo descripción no puede estar vacío")
            return _Classification(subject_text, description_text)

        if subject_id is None:
            raise InvalidInputError("Selecciona el asunto")
        subject_ref = next((item for item in offered if item.id == subject_id), None)
        if subject_ref is None:
            raise InvalidInputError("El asunto no está disponible para este tipo de solicitud")
        cause_ref = None
        if subject_ref.causes:
            if cause_id is None:
                raise InvalidInputError("Selecciona la causal")
            cause_ref = next((item for item in subject_ref.causes if item.id == cause_id), None)
            if cause_ref is None:
                raise InvalidInputError("La causal no pertenece al asunto seleccionado")
        elif cause_id is not None:
            raise InvalidInputError("El asunto seleccionado no tiene causales")
        if (subject_ref.is_other or (cause_ref and cause_ref.is_other)) and not description_text:
            raise InvalidInputError("Describe la novedad: elegiste «Otro»")

        allows_product = cause_ref is not None and cause_ref.allows_product
        if products and not allows_product:
            raise InvalidInputError("La causal seleccionada no admite productos")
        if cause_ref is not None and cause_ref.requires_product and not products:
            raise InvalidInputError("Agrega al menos un producto: la causal lo exige")
        claimed = await self._claimed_products(products)
        number = clean_optional(pickup_order_number, "número de orden de recogida", 40)
        if not claimed:
            if pickup_order_requested or number:
                raise InvalidInputError("La orden de recogida solo se indica con productos")
        elif pickup_order_requested not in PICKUP_ORDER_ANSWERS:
            raise InvalidInputError("Indica si ya solicitaste la orden de recogida")
        elif number and pickup_order_requested != "yes":
            raise InvalidInputError("El número de orden de recogida va solo si ya la solicitaste")
        return _Classification(
            subject_text,
            description_text,
            subject_ref,
            cause_ref,
            claimed,
            pickup_order_requested if claimed else None,
            number,
        )

    async def _claimed_products(self, products: Sequence[ProductInput]) -> list[ClaimedProduct]:
        """Clean the product lines and copy each reference's name and supplier."""

        if len(products) > _MAX_PRODUCTS:
            raise InvalidInputError(f"Máximo {_MAX_PRODUCTS} productos por solicitud")
        lines = []
        for position, product in enumerate(products, start=1):
            if not 0 < product.affected_quantity <= _MAX_AFFECTED_QUANTITY:
                raise InvalidInputError(
                    f"La cantidad afectada del producto {position} debe ser mayor que cero"
                )
            lines.append(
                ProductInput(
                    invoice_number=clean_required(product.invoice_number, "número de factura", 40),
                    sku=clean_required(product.sku, "referencia", 40).upper(),
                    lot=clean_required(product.lot, "lote", 60),
                    expiry_date=product.expiry_date,
                    affected_quantity=product.affected_quantity,
                )
            )
        if not lines:
            return []
        masters: dict[str, ProductMaster] = {}
        if self.products is not None:
            masters = await self.products.resolve([line.sku for line in lines])
            missing = [line.sku for line in lines if line.sku not in masters]
            if missing:
                raise InvalidInputError(
                    f"La referencia {missing[0]} no existe en el maestro de productos"
                )
        return [
            ClaimedProduct(
                invoice_number=line.invoice_number,
                sku=line.sku,
                lot=line.lot,
                expiry_date=line.expiry_date,
                affected_quantity=line.affected_quantity,
                description=masters[line.sku].description if line.sku in masters else None,
                supplier_code=masters[line.sku].supplier_code if line.sku in masters else None,
                supplier_name=masters[line.sku].supplier_name if line.sku in masters else None,
            )
            for line in lines
        ]

    async def _requested_documents(
        self,
        request_type: RequestType,
        images: Sequence[ImageUpload],
        document_request: bool,
        category_id: UUID | None,
        type_ids: Sequence[UUID],
        other: str | None,
    ) -> list[RequestedDocument]:
        """Validate the documents of a document request against the public catalog."""

        other_value = clean_optional(other, "otro documento", 300)
        if not document_request:
            if category_id is not None or type_ids or other_value is not None:
                raise InvalidInputError(
                    "Los documentos solo se indican en una solicitud de documentos"
                )
            return []
        if not request_type.allows_document_request:
            raise InvalidInputError("Este tipo de solicitud no admite solicitud de documentos")
        if images:
            raise InvalidInputError("Una solicitud de documentos no admite evidencias")
        if category_id is None:
            raise InvalidInputError("Selecciona la categoría del documento")
        category = next(
            (
                item
                for item in await self.repository.list_document_categories()
                if item.id == category_id
            ),
            None,
        )
        if category is None:
            raise InvalidInputError("La categoría del documento no está disponible")
        if len(set(type_ids)) != len(type_ids):
            raise InvalidInputError("Un documento no puede repetirse")
        offered = {document.id for document in category.documents}
        if any(type_id not in offered for type_id in type_ids):
            raise InvalidInputError("Un documento no pertenece a la categoría seleccionada")
        documents = [RequestedDocument(document_type_id=type_id) for type_id in type_ids]
        if other_value is not None:
            documents.append(RequestedDocument(document_type_id=None, description=other_value))
        if not documents:
            raise InvalidInputError("Indica qué documento necesitas")
        if len(documents) > _MAX_DOCUMENTS:
            raise InvalidInputError(f"Máximo {_MAX_DOCUMENTS} documentos por solicitud")
        return documents

    async def _process_images(
        self, allows_attachments: bool, images: Sequence[ImageUpload]
    ) -> list[tuple[SanitizedImage, str]]:
        """Sanitize and scan every image before the request is persisted."""

        if not images:
            return []
        if not allows_attachments:
            raise InvalidInputError("Este tipo de solicitud no admite imágenes")
        if len(images) > self.max_attachments:
            raise InvalidInputError(f"Máximo {self.max_attachments} imágenes por solicitud")
        if self.image_processor is None or self.malware_scanner is None:
            raise DependencyUnavailableError("La validación de imágenes no está configurada")
        total_bytes = 0
        processed: list[tuple[SanitizedImage, str]] = []
        for upload in images:
            if len(upload.content) > self.max_attachment_bytes:
                raise InvalidInputError("Una imagen supera el tamaño máximo permitido")
            total_bytes += len(upload.content)
            if total_bytes > self.max_total_attachment_bytes:
                raise InvalidInputError("El total de imágenes supera el tamaño máximo permitido")
            image = await self.image_processor.sanitize(
                upload.filename, upload.content_type, upload.content
            )
            await self.malware_scanner.scan(image.content)
            processed.append((image, _checksum(image.content)))
        return processed

    async def _process_videos(
        self, allows_attachments: bool, videos: Sequence[ImageUpload]
    ) -> list[tuple[SanitizedImage, str]]:
        """Check and scan every evidence video before the request is persisted."""

        if not videos:
            return []
        if not allows_attachments:
            raise InvalidInputError("Este tipo de solicitud no admite videos")
        if len(videos) > self.max_videos:
            raise InvalidInputError(f"Máximo {self.max_videos} video(s) por solicitud")
        if self.video_inspector is None or self.malware_scanner is None:
            raise DependencyUnavailableError("La validación de videos no está configurada")
        processed: list[tuple[SanitizedImage, str]] = []
        for upload in videos:
            if len(upload.content) > self.max_video_bytes:
                raise InvalidInputError("Un video supera el tamaño máximo permitido")
            video = await self.video_inspector.inspect(
                upload.filename, upload.content_type, upload.content
            )
            await self.malware_scanner.scan(video.content)
            processed.append((video, _checksum(video.content)))
        return processed

    async def get_request(self, request_id: UUID) -> PqrsfRequest:
        """Return one administrator request."""

        request = await self.repository.get_request(request_id)
        if request is None:
            raise NotFoundError("Solicitud PQRSF no encontrada")
        return request

    async def list_requests(self, filters: PqrsfFilters, page: int, size: int) -> PqrsfPage:
        """Return one administrator page with bounded pagination."""

        return await self.repository.list_requests(filters, max(1, page), min(max(1, size), 100))

    async def download_attachment(
        self, request_id: UUID, attachment_id: UUID
    ) -> tuple[PqrsfAttachment, bytes]:
        """Read one private image for an administrator."""

        if await self.repository.get_request(request_id) is None:
            raise NotFoundError("Solicitud PQRSF no encontrada")
        attachment = await self.repository.get_attachment(request_id, attachment_id)
        if attachment is None:
            raise NotFoundError("Adjunto PQRSF no encontrado")
        if self.storage is None:
            raise DependencyUnavailableError("El almacenamiento de imágenes no está configurado")
        try:
            content = await self.storage.get_object(attachment.storage_key)
        except Exception as exc:
            raise DependencyUnavailableError("El almacenamiento de imágenes no responde") from exc
        return attachment, content


def _payload_hash(payload: Mapping[str, object]) -> str:
    """Hash canonical form values and sanitized attachment descriptors."""

    encoded = json.dumps(
        payload, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

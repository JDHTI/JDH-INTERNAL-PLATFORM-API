"""In-memory adapters shared by the module tests (no PostgreSQL, Redis or S3)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

from app.core.errors import ConflictError, NotFoundError
from app.modules.areas.domain.entities import Area, AreaMember, AreaRole
from app.modules.documents.domain.entities import Document, DocumentFolder
from app.modules.documents.domain.ports import SearchFilters
from app.modules.identity.domain.principal import DirectoryUser, Principal

NOW = datetime(2026, 9, 23, tzinfo=UTC)


def principal(email: str = "ana@jdh.com.co", admin: bool = False, user_id=None) -> Principal:
    return Principal(
        user_id=user_id or uuid.uuid4(), email=email, display_name=None, is_platform_admin=admin
    )


class FakeDirectory:
    """Users as the identity module would expose them after Entra sign-in."""

    def __init__(self) -> None:
        self.users: dict[uuid.UUID, DirectoryUser] = {}

    def sign_in(self, email: str, admin: bool = False, is_active: bool = True) -> Principal:
        """Register a user the way a first sign-in does and return their principal."""

        user = next((u for u in self.users.values() if u.email == email.lower()), None)
        if user is None:
            user = DirectoryUser(
                id=uuid.uuid4(), email=email.lower(), display_name=None, is_active=is_active
            )
            self.users[user.id] = user
        elif user.is_active != is_active:
            self.users[user.id] = DirectoryUser(
                id=user.id,
                email=user.email,
                display_name=user.display_name,
                is_active=is_active,
            )
        return principal(user.email, admin=admin, user_id=user.id)

    async def require_user(self, email: str) -> DirectoryUser:
        user = next((u for u in self.users.values() if u.email == email.strip().lower()), None)
        if user is None:
            raise NotFoundError(f"No hay un usuario con el correo {email}")
        return user

    async def get_users(self, user_ids):
        return {i: self.users[i] for i in user_ids if i in self.users}


class FakeAreaRepository:
    def __init__(self) -> None:
        self.areas: dict[uuid.UUID, Area] = {}
        self.roles: dict[uuid.UUID, AreaRole] = {}
        self.members: dict[uuid.UUID, AreaMember] = {}
        self.areas_with_folders: set[uuid.UUID] = set()

    async def list_areas(self):
        return sorted(self.areas.values(), key=lambda a: (a.sort_order, a.name))

    async def list_areas_for_user(self, user_id):
        rows = []
        for area in await self.list_areas():
            member = await self.get_member_by_user(area.id, user_id)
            if member or area.responsible_user_id == user_id:
                rows.append((area, self.roles[member.role_id] if member else None))
        return rows

    async def get_by_slug(self, slug):
        return next((a for a in self.areas.values() if a.slug == slug), None)

    async def get_by_id(self, area_id):
        return self.areas.get(area_id)

    async def find_conflicting_area(self, slug, name, exclude_id=None):
        for area in self.areas.values():
            if area.id == exclude_id:
                continue
            if (slug and area.slug == slug) or (name and area.name.lower() == name.lower()):
                return area
        return None

    async def create_area(self, slug, name, remit, icon, sort_order, responsible_user_id):
        area = Area(
            uuid.uuid4(), slug, name, remit, icon, sort_order, responsible_user_id, NOW, NOW
        )
        self.areas[area.id] = area
        return area

    async def save_area(self, area):
        self.areas[area.id] = replace(area)
        return self.areas[area.id]

    async def delete_area(self, area_id):
        if area_id in self.areas_with_folders:
            raise ConflictError(
                "El área tiene carpetas de documentos: elimínalas antes de borrarla"
            )
        self.areas.pop(area_id)
        self.roles = {k: r for k, r in self.roles.items() if r.area_id != area_id}
        self.members = {k: m for k, m in self.members.items() if m.area_id != area_id}

    async def list_roles(self, area_id):
        return sorted(
            (r for r in self.roles.values() if r.area_id == area_id), key=lambda r: r.name
        )

    async def get_role(self, area_id, role_id):
        role = self.roles.get(role_id)
        return role if role and role.area_id == area_id else None

    async def role_name_taken(self, area_id, name, exclude_id=None):
        return any(
            r.area_id == area_id and r.name.lower() == name.lower() and r.id != exclude_id
            for r in self.roles.values()
        )

    async def create_role(self, area_id, name):
        role = AreaRole(uuid.uuid4(), area_id, name, NOW, NOW)
        self.roles[role.id] = role
        return role

    async def save_role(self, role):
        self.roles[role.id] = role
        return role

    async def delete_role(self, role_id):
        self.roles.pop(role_id)

    async def role_in_use(self, role_id):
        return any(m.role_id == role_id for m in self.members.values())

    async def list_members(self, area_id):
        return [m for m in self.members.values() if m.area_id == area_id]

    async def get_member(self, area_id, member_id):
        member = self.members.get(member_id)
        return member if member and member.area_id == area_id else None

    async def get_member_by_user(self, area_id, user_id):
        return next(
            (m for m in self.members.values() if m.area_id == area_id and m.user_id == user_id),
            None,
        )

    async def create_member(self, area_id, user_id, role_id):
        member = AreaMember(uuid.uuid4(), area_id, user_id, role_id, NOW, NOW)
        self.members[member.id] = member
        return member

    async def save_member(self, member):
        self.members[member.id] = member
        return member

    async def delete_member(self, member_id):
        self.members.pop(member_id)


class FakeDocumentRepository:
    def __init__(self) -> None:
        self.folders: dict[uuid.UUID, DocumentFolder] = {}
        self.documents: dict[uuid.UUID, Document] = {}
        self.fail_next_add: Exception | None = None
        self.last_filters: SearchFilters | None = None

    async def list_folders(self, area_id):
        return [
            (f, sum(1 for d in self.documents.values() if d.folder_id == f.id))
            for f in self.folders.values()
            if f.area_id == area_id
        ]

    async def get_folder(self, area_id, folder_id):
        folder = self.folders.get(folder_id)
        return folder if folder and folder.area_id == area_id else None

    async def folder_name_taken(self, area_id, name, exclude_id=None):
        return any(
            f.area_id == area_id and f.name.lower() == name.lower() and f.id != exclude_id
            for f in self.folders.values()
        )

    async def create_folder(self, area_id, name, description, created_by):
        folder = DocumentFolder(uuid.uuid4(), area_id, name, description, created_by, NOW, NOW)
        self.folders[folder.id] = folder
        return folder

    async def save_folder(self, folder):
        self.folders[folder.id] = folder
        return folder

    async def delete_folder(self, folder_id):
        self.folders.pop(folder_id)

    async def count_folder_documents(self, folder_id):
        return sum(1 for d in self.documents.values() if d.folder_id == folder_id)

    async def get_document(self, document_id):
        return self.documents.get(document_id)

    async def document_name_taken(self, folder_id, name, exclude_id=None):
        return any(
            d.folder_id == folder_id and d.name.lower() == name.lower() and d.id != exclude_id
            for d in self.documents.values()
        )

    async def add_document(self, document):
        if self.fail_next_add:
            exc, self.fail_next_add = self.fail_next_add, None
            raise exc
        self.documents[document.id] = document
        return document

    async def save_document(self, document):
        self.documents[document.id] = document
        return document

    async def delete_document(self, document_id):
        self.documents.pop(document_id)

    async def search(self, filters, page, size):
        self.last_filters = filters
        items = [
            d
            for d in self.documents.values()
            if filters.area_ids is None or d.area_id in filters.area_ids
        ]
        return items, len(items)


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str | None]] = {}
        self.deleted: list[str] = []

    async def put_object(self, key, body, content_type=None):
        self.objects[key] = (body, content_type)

    async def delete_object(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)

    def presigned_download_url(self, key, filename, expires_in):
        return f"https://s3.test/{key}?filename={filename}&ttl={expires_in}"

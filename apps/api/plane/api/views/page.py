# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db.models import Q
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ProjectMemberPermission, WorkspaceEntityPermission
from plane.app.views.page.base import unarchive_archive_page_and_descendants
from plane.bgtasks.page_transaction_task import page_transaction
from plane.db.models import Page, Project, ProjectMember, ProjectPage, UserFavorite, UserRecentVisit, Workspace

from .base import BaseAPIView


def _page_payload(page, description=False):
    """Shape the page the way plane-sdk's Page model reads it."""
    data = {
        "id": str(page.id),
        "name": page.name,
        "description_stripped": page.description_stripped,
        "owned_by": str(page.owned_by_id),
        "access": page.access,
        "color": page.color,
        "is_locked": page.is_locked,
        "archived_at": page.archived_at,
        "parent_id": str(page.parent_id) if page.parent_id else None,
        "workspace": str(page.workspace_id),
        "projects": [str(project_id) for project_id in page.projects.values_list("id", flat=True)],
        "external_id": page.external_id,
        "external_source": page.external_source,
        "created_at": page.created_at,
        "updated_at": page.updated_at,
    }
    if description:
        data["description_html"] = page.description_html
    return data


def _visible_pages(user, slug, project_id=None):
    """Pages the user may see: public ones, plus their own private ones.

    Guests of a project that hides features from guests only see their own.
    """
    queryset = Page.objects.filter(workspace__slug=slug).filter(Q(owned_by=user) | Q(access=Page.PUBLIC_ACCESS))
    if project_id is not None:
        queryset = queryset.filter(
            project_pages__project_id=project_id,
            project_pages__deleted_at__isnull=True,
            project_pages__project__archived_at__isnull=True,
        )
        project = Project.objects.filter(pk=project_id, workspace__slug=slug).first()
        is_guest = ProjectMember.objects.filter(
            workspace__slug=slug,
            project_id=project_id,
            member=user,
            role=5,
            is_active=True,
        ).exists()
        if project is not None and is_guest and not project.guest_view_all_features:
            queryset = queryset.filter(owned_by=user)
    else:
        # A workspace page is one filed in no project.
        queryset = queryset.filter(is_global=True).filter(project_pages__isnull=True)
    return queryset.distinct()


def _get_page(user, slug, page_id, project_id=None):
    return _visible_pages(user, slug, project_id).filter(pk=page_id).first()


def _can_edit(user, slug, page, project_id=None):
    """The page owner, or an admin of its project or workspace, may change it."""
    if page.owned_by_id == user.id:
        return True
    if project_id is not None:
        return ProjectMember.objects.filter(
            workspace__slug=slug, project_id=project_id, member=user, role=20, is_active=True
        ).exists()
    return Workspace.objects.filter(
        slug=slug, workspace_member__member=user, workspace_member__role=20, workspace_member__is_active=True
    ).exists()


class PageAPIEndpoint(BaseAPIView):
    """Project and workspace pages over the API key.

    plane-sdk addresses both through one resource: with a project id the page
    belongs to that project, without one it is a workspace page. The community
    app only serves pages to a browser session, so this is the key-authenticated
    door onto the same Page rows.
    """

    use_read_replica = True

    def get_permissions(self):
        permission = ProjectMemberPermission if self.kwargs.get("project_id") else WorkspaceEntityPermission
        return [permission()]

    def get(self, request, slug, page_id=None, project_id=None):
        if page_id is None:
            queryset = _visible_pages(request.user, slug, project_id).order_by("-created_at")
            return self.paginate(
                request=request,
                queryset=queryset,
                on_results=lambda pages: [_page_payload(page) for page in pages],
            )

        page = _get_page(request.user, slug, page_id, project_id)
        if page is None:
            return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(_page_payload(page, description=True), status=status.HTTP_200_OK)

    def post(self, request, slug, project_id=None):
        name = request.data.get("name")
        description_html = request.data.get("description_html")
        if not name or description_html is None:
            return Response(
                {"error": "name and description_html are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        parent_id = request.data.get("parent_id") or None
        if parent_id:
            parent = _get_page(request.user, slug, parent_id, project_id)
            if parent is None:
                return Response({"error": "Parent page not found"}, status=status.HTTP_400_BAD_REQUEST)

        workspace = Workspace.objects.get(slug=slug)
        page = Page.objects.create(
            name=name,
            description_html=description_html,
            owned_by=request.user,
            workspace=workspace,
            access=request.data.get("access", Page.PUBLIC_ACCESS),
            color=request.data.get("color", ""),
            is_locked=bool(request.data.get("is_locked", False)),
            parent_id=parent_id,
            is_global=project_id is None,
            external_id=request.data.get("external_id") or None,
            external_source=request.data.get("external_source") or None,
        )
        if project_id is not None:
            ProjectPage.objects.create(workspace=workspace, project_id=project_id, page=page)

        page_transaction.delay(
            new_description_html=description_html,
            old_description_html=None,
            page_id=page.id,
        )
        return Response(_page_payload(page, description=True), status=status.HTTP_201_CREATED)

    def patch(self, request, slug, page_id, project_id=None):
        page = _get_page(request.user, slug, page_id, project_id)
        if page is None:
            return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
        if page.is_locked:
            return Response({"error": "Page is locked"}, status=status.HTTP_400_BAD_REQUEST)
        if page.archived_at is not None:
            return Response({"error": "Page is archived"}, status=status.HTTP_400_BAD_REQUEST)
        if not _can_edit(request.user, slug, page, project_id):
            return Response(
                {"error": "Only the owner or an admin can update the page"},
                status=status.HTTP_403_FORBIDDEN,
            )

        previous_html = page.description_html
        for field in ("name", "description_html", "access", "color", "is_locked", "external_id", "external_source"):
            if field in request.data:
                setattr(page, field, request.data.get(field))
        page.save()

        if "description_html" in request.data:
            page_transaction.delay(
                new_description_html=page.description_html,
                old_description_html=previous_html,
                page_id=page.id,
            )
        return Response(_page_payload(page, description=True), status=status.HTTP_200_OK)

    def delete(self, request, slug, page_id, project_id=None):
        page = _get_page(request.user, slug, page_id, project_id)
        if page is None:
            return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
        if page.archived_at is None:
            return Response(
                {"error": "The page should be archived before deleting"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _can_edit(request.user, slug, page, project_id):
            return Response(
                {"error": "Only the owner or an admin can delete the page"},
                status=status.HTTP_403_FORBIDDEN,
            )

        Page.objects.filter(parent_id=page.id).update(parent=None)
        page.delete()
        UserFavorite.objects.filter(workspace__slug=slug, entity_identifier=page_id, entity_type="page").delete()
        UserRecentVisit.objects.filter(workspace__slug=slug, entity_identifier=page_id, entity_name="page").delete(
            soft=False
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class PageArchiveAPIEndpoint(BaseAPIView):
    """Archive with POST, restore with DELETE, matching plane-sdk."""

    def get_permissions(self):
        permission = ProjectMemberPermission if self.kwargs.get("project_id") else WorkspaceEntityPermission
        return [permission()]

    def post(self, request, slug, page_id, project_id=None):
        return self._set_archived(request, slug, page_id, project_id, timezone.now().date())

    def delete(self, request, slug, page_id, project_id=None):
        return self._set_archived(request, slug, page_id, project_id, None)

    def _set_archived(self, request, slug, page_id, project_id, archived_at):
        page = _get_page(request.user, slug, page_id, project_id)
        if page is None:
            return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
        if not _can_edit(request.user, slug, page, project_id):
            return Response(
                {"error": "Only the owner or an admin can archive the page"}, status=status.HTTP_403_FORBIDDEN
            )

        # Restoring a page under an archived parent would leave it unreachable.
        if archived_at is None and page.parent_id and page.parent.archived_at:
            page.parent = None
            page.save(update_fields=["parent"])

        if archived_at is not None:
            UserFavorite.objects.filter(workspace__slug=slug, entity_identifier=page_id, entity_type="page").delete()

        unarchive_archive_page_and_descendants(page_id, archived_at)
        return Response({"page_id": str(page_id), "archived": archived_at is not None}, status=status.HTTP_200_OK)

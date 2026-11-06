# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import APIToken, Page, Project, ProjectMember, ProjectPage


@pytest.fixture
def project(db, workspace, create_user):
    """A project the API key's user administers."""
    project = Project.objects.create(
        name="Test Project",
        identifier="TP",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


def _pages_url(workspace_slug, project_id=None):
    if project_id is None:
        return f"/api/v1/workspaces/{workspace_slug}/pages/"
    return f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/pages/"


@pytest.mark.contract
class TestPageAPI:
    """The key-authenticated page API the official MCP server calls."""

    @pytest.mark.django_db
    def test_project_page_round_trip(self, api_key_client, workspace, project):
        url = _pages_url(workspace.slug, project.id)

        created = api_key_client.post(url, {"name": "Notes", "description_html": "<p>hello</p>"}, format="json")
        assert created.status_code == status.HTTP_201_CREATED, created.data
        page_id = created.data["id"]
        assert created.data["name"] == "Notes"
        assert created.data["description_html"] == "<p>hello</p>"
        assert created.data["projects"] == [str(project.id)]

        # The row is a real project page, so the app's own pages list shows it.
        assert ProjectPage.objects.filter(project=project, page_id=page_id, deleted_at__isnull=True).exists()

        fetched = api_key_client.get(f"{url}{page_id}/")
        assert fetched.status_code == status.HTTP_200_OK
        assert fetched.data["description_stripped"] == "hello"

        renamed = api_key_client.patch(f"{url}{page_id}/", {"name": "Notes v2"}, format="json")
        assert renamed.status_code == status.HTTP_200_OK
        assert renamed.data["name"] == "Notes v2"

        listed = api_key_client.get(url)
        assert listed.status_code == status.HTTP_200_OK
        assert page_id in [row["id"] for row in listed.data["results"]]

        # Deleting an unarchived page is refused, matching the app.
        refused = api_key_client.delete(f"{url}{page_id}/")
        assert refused.status_code == status.HTTP_400_BAD_REQUEST

        archived = api_key_client.post(f"{url}{page_id}/archive/")
        assert archived.status_code == status.HTTP_200_OK
        assert archived.data["archived"] is True

        deleted = api_key_client.delete(f"{url}{page_id}/")
        assert deleted.status_code == status.HTTP_204_NO_CONTENT
        assert Page.objects.get(pk=page_id).deleted_at is not None

    @pytest.mark.django_db
    def test_workspace_page(self, api_key_client, workspace):
        url = _pages_url(workspace.slug)

        created = api_key_client.post(url, {"name": "Wiki", "description_html": "<p>home</p>"}, format="json")
        assert created.status_code == status.HTTP_201_CREATED, created.data
        page = Page.objects.get(pk=created.data["id"])
        assert page.is_global is True
        assert page.projects.count() == 0

        listed = api_key_client.get(url)
        assert created.data["id"] in [row["id"] for row in listed.data["results"]]

    @pytest.mark.django_db
    def test_private_page_is_hidden_from_other_members(self, api_key_client, workspace, project, create_user):
        owner = ProjectMember.objects.filter(project=project).first().member
        page = Page.objects.create(
            name="Secret",
            description_html="<p>nope</p>",
            owned_by=owner,
            workspace=workspace,
            access=Page.PRIVATE_ACCESS,
        )
        ProjectPage.objects.create(workspace=workspace, project=project, page=page)

        # The owner sees their own private page.
        own = api_key_client.get(f"{_pages_url(workspace.slug, project.id)}{page.id}/")
        assert own.status_code == status.HTTP_200_OK

        # A different member of the same project does not.
        other = create_user.__class__.objects.create(email="other@example.com", username="other")
        ProjectMember.objects.create(project=project, member=other, role=15, is_active=True)
        token = APIToken.objects.create(user=other, label="other")
        api_key_client.credentials(HTTP_X_API_KEY=token.token)

        hidden = api_key_client.get(f"{_pages_url(workspace.slug, project.id)}{page.id}/")
        assert hidden.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_requires_api_key(self, api_client, workspace, project):
        response = api_client.get(_pages_url(workspace.slug, project.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

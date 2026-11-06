# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.api.views.page import PageAPIEndpoint, PageArchiveAPIEndpoint

urlpatterns = [
    path(
        "workspaces/<str:slug>/pages/",
        PageAPIEndpoint.as_view(http_method_names=["get", "post"]),
        name="workspace-pages",
    ),
    path(
        "workspaces/<str:slug>/pages/<uuid:page_id>/",
        PageAPIEndpoint.as_view(http_method_names=["get", "patch", "delete"]),
        name="workspace-page",
    ),
    path(
        "workspaces/<str:slug>/pages/<uuid:page_id>/archive/",
        PageArchiveAPIEndpoint.as_view(http_method_names=["post", "delete"]),
        name="workspace-page-archive",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/pages/",
        PageAPIEndpoint.as_view(http_method_names=["get", "post"]),
        name="project-pages",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/pages/<uuid:page_id>/",
        PageAPIEndpoint.as_view(http_method_names=["get", "patch", "delete"]),
        name="project-page",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/pages/<uuid:page_id>/archive/",
        PageArchiveAPIEndpoint.as_view(http_method_names=["post", "delete"]),
        name="project-page-archive",
    ),
]

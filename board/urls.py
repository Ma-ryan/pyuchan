from django.urls import path
from . import views

app_name = 'board'

urlpatterns = [
    # Root route: redirects '/' to the first available board
    path('', views.root_index, name='root'),

    # Utility endpoints
    path('captcha/', views.captcha_image, name='captcha_image'),

    # Board views
    path('<slug:uri>/', views.board_index, name='index'),
    path('<slug:uri>/<int:page>/', views.board_index, name='index_paginated'),
    path('<slug:uri>/catalog/', views.catalog, name='catalog'),
    path('<slug:uri>/archive/', views.archive_view, name='archive'),
    path('<slug:uri>/res/<int:thread_id>/', views.thread_detail, name='thread'),
    path('<str:uri>/res/<int:thread_id>/updates/', views.thread_updates, name='thread_updates'),

    # Post deletion and reporting
    path('<slug:uri>/delete/', views.delete_post_view, name='delete_post'),
    path('<slug:uri>/report/', views.report_post, name='report_post'),

    # Staff moderation actions
    path('<slug:uri>/mod/delete/', views.mod_delete_post, name='mod_delete'),
    path('<slug:uri>/mod/ban/', views.mod_ban_user, name='mod_ban'),
    path('<slug:uri>/mod/notice/', views.mod_notice_post, name='mod_notice'),
    path('<slug:uri>/mod/thread/<int:thread_id>/move/', views.mod_move_thread, name='mod_move_thread'),
    path('<slug:uri>/mod/thread/<int:thread_id>/<str:action>/', views.mod_toggle_thread, name='mod_toggle_thread'),
]

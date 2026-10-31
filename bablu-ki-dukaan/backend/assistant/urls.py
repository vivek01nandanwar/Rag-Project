from django.urls import path

from . import views

urlpatterns = [
    path("chat/", views.ChatView.as_view()),
    path("actions/<str:pk>/confirm/", views.ConfirmView.as_view()),
    path("actions/<str:pk>/cancel/", views.CancelView.as_view()),
    path("sessions/", views.SessionListView.as_view()),
    path("sessions/<str:pk>/", views.SessionDetailView.as_view()),
    path("status/", views.StatusView.as_view()),
    path("registers/", views.RegistersView.as_view()),
]
